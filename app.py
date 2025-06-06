
import os
import openai
from flask import Flask, request, render_template, redirect, url_for, flash, send_file, jsonify
from werkzeug.utils import secure_filename
from docx import Document
from reportlab.lib.pagesizes import letter
from reportlab.pdfgen import canvas
from reportlab.lib.styles import getSampleStyleSheet
from reportlab.platypus import SimpleDocTemplate, Paragraph, Spacer
from reportlab.lib.units import inch
from reportlab.lib import colors
import threading
import time

app = Flask(__name__)
app.secret_key = os.environ.get('FLASK_SECRET_KEY', 'your-secret-key-here')

# Configuration
UPLOAD_FOLDER = 'uploads'
FEEDBACK_FOLDER = 'feedback'
ALLOWED_EXTENSIONS = {'docx'}

# Ensure directories exist
os.makedirs(UPLOAD_FOLDER, exist_ok=True)
os.makedirs(FEEDBACK_FOLDER, exist_ok=True)

# Store processing status
processing_status = {}

def allowed_file(filename):
    return '.' in filename and filename.rsplit('.', 1)[1].lower() in ALLOWED_EXTENSIONS

def extract_text_from_docx(file_path):
    """Extract text from a DOCX file"""
    try:
        doc = Document(file_path)
        return "\n".join([para.text for para in doc.paragraphs])
    except Exception as e:
        raise Exception(f"Failed to read document: {str(e)}")

def grade_essay_with_ai(essay_content, task_id):
    """Grade essay using OpenAI API"""
    try:
        processing_status[task_id] = {"status": "processing", "progress": "Connecting to AI..."}
        
        if not os.getenv("OPENAI_API_KEY"):
            processing_status[task_id] = {"status": "error", "message": "OpenAI API key not configured"}
            return None
        
        client = openai.OpenAI(api_key=os.getenv("OPENAI_API_KEY"))
        
        processing_status[task_id] = {"status": "processing", "progress": "Analyzing essay..."}
        
        system_prompt = """You are an English language instructor evaluating undergraduate entrance essays responding to the following question:
"Mythology does not inspire creativity as it only encourages the recycling of old stories." Do you agree?

Your task is to evaluate the essays based on the criteria below and provide a grade:

1. Content Analysis, Originality, and Critical Thinking (30%)
   - Assess relevance, depth of analysis, originality, and use of evidence
2. Structure and Organization (30%)
   - Introduction with thesis, logical flow, topic sentences, and conclusion
3. Source Crediting and Paraphrasing (10%)
   - Evaluate paraphrasing and crediting of sources. Avoid direct lifts
4. Language and Grammar (30%)
   - Evaluate clarity, effectiveness, and grammatical correctness

Grading scheme:
- A (90-100): Excellent, original, accurate, and well-structured
- B (80-89): Good, mostly original, clear with minor issues
- C (70-79): Adequate, limited originality, noticeable errors
- D (60-69): Poor structure, unoriginal, many errors
- F (Below 60): Major issues, copied content, incoherent

You must provide:
- Percent score per criterion
- Overall percentage and grade
- A detailed feedback summary"""

        response = client.chat.completions.create(
            model="gpt-4",
            messages=[
                {"role": "system", "content": system_prompt},
                {"role": "user", "content": f"Evaluate the following student essay:\n\n{essay_content}"}
            ]
        )
        
        feedback = response.choices[0].message.content
        processing_status[task_id].update({"status": "completed", "feedback": feedback})
        return feedback
        
    except Exception as e:
        processing_status[task_id].update({"status": "error", "message": str(e)})
        return None

def create_pdf_feedback(feedback_content, filename):
    """Create PDF from feedback content"""
    try:
        pdf_path = os.path.join(FEEDBACK_FOLDER, f"{filename}_feedback.pdf")
        
        doc = SimpleDocTemplate(pdf_path, pagesize=letter,
                              rightMargin=72, leftMargin=72,
                              topMargin=72, bottomMargin=18)
        styles = getSampleStyleSheet()
        story = []
        
        # Custom styles
        title_style = styles['Title']
        title_style.fontSize = 18
        title_style.spaceAfter = 20
        title_style.textColor = colors.darkblue
        
        normal_style = styles['Normal']
        normal_style.fontSize = 11
        normal_style.spaceAfter = 12
        normal_style.alignment = 0
        
        # Title
        title = Paragraph("AI Essay Grading Feedback", title_style)
        story.append(title)
        story.append(Spacer(1, 20))
        
        # Feedback content
        feedback_lines = feedback_content.split('\n')
        for line in feedback_lines:
            line = line.strip()
            if line:
                # Escape HTML characters
                line = line.replace('&', '&amp;').replace('<', '&lt;').replace('>', '&gt;')
                # Handle bullet points
                if line.startswith('- ') or line.startswith('• '):
                    line = f"&bull; {line[2:]}"
                elif line.startswith('*'):
                    line = f"&bull; {line[1:].strip()}"
                
                try:
                    p = Paragraph(line, normal_style)
                    story.append(p)
                except:
                    # Fallback for problematic text
                    safe_line = ''.join(char for char in line if ord(char) < 127)
                    p = Paragraph(safe_line, normal_style)
                    story.append(p)
                
                story.append(Spacer(1, 6))
        
        doc.build(story)
        return pdf_path
        
    except Exception as e:
        raise Exception(f"Failed to create PDF: {str(e)}")

@app.route('/')
def index():
    return render_template('index.html')

@app.route('/upload', methods=['POST'])
def upload_file():
    if 'file' not in request.files:
        flash('No file selected')
        return redirect(request.url)
    
    file = request.files['file']
    if file.filename == '':
        flash('No file selected')
        return redirect(request.url)
    
    if file and allowed_file(file.filename):
        filename = secure_filename(file.filename)
        # Generate unique filename to avoid conflicts
        timestamp = str(int(time.time()))
        base_name = os.path.splitext(filename)[0]
        unique_filename = f"{base_name}_{timestamp}.docx"
        file_path = os.path.join(UPLOAD_FOLDER, unique_filename)
        file.save(file_path)
        
        try:
            # Extract text from document
            essay_content = extract_text_from_docx(file_path)
            
            # Start grading in background
            task_id = f"task_{timestamp}"
            processing_status[task_id] = {"status": "starting", "filename": base_name}
            
            threading.Thread(
                target=grade_essay_with_ai, 
                args=(essay_content, task_id), 
                daemon=True
            ).start()
            
            return render_template('processing.html', task_id=task_id, filename=base_name)
            
        except Exception as e:
            flash(f'Error processing file: {str(e)}')
            return redirect(url_for('index'))
    else:
        flash('Please upload a .docx file')
        return redirect(url_for('index'))

@app.route('/status/<task_id>')
def check_status(task_id):
    if task_id in processing_status:
        return jsonify(processing_status[task_id])
    return jsonify({"status": "not_found"})

@app.route('/result/<task_id>')
def show_result(task_id):
    if task_id not in processing_status:
        flash('Task not found')
        return redirect(url_for('index'))
    
    task_data = processing_status[task_id]
    if task_data["status"] != "completed":
        flash('Task not completed yet')
        return redirect(url_for('index'))
    
    # Safely get filename with fallback
    filename = task_data.get("filename", f"task_{task_id}")

    return render_template('result.html', 
                         feedback=task_data["feedback"], 
                         task_id=task_id,
                         filename=filename)

@app.route('/download/<task_id>')
def download_feedback(task_id):
    if task_id not in processing_status:
        flash('Task not found')
        return redirect(url_for('index'))
    
    task_data = processing_status[task_id]
    if task_data["status"] != "completed":
        flash('Feedback not ready yet')
        return redirect(url_for('index'))
    
    try:
        # Create PDF
        pdf_path = create_pdf_feedback(task_data["feedback"], task_data["filename"])
        return send_file(pdf_path, as_attachment=True, download_name=f"{task_data['filename']}_feedback.pdf")
    except Exception as e:
        flash(f'Error creating PDF: {str(e)}')
        return redirect(url_for('show_result', task_id=task_id))

if __name__ == '__main__':
    app.run(host='0.0.0.0', port=5000, debug=True)
