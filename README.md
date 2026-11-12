# ติดตั้ง FFmpeg
winget install Gyan.FFmpeg

# สร้าง Virtual Environment
python -m venv .venv

# เปิดใช้งาน Virtual Environment
.venv\Scripts\activate

# อัปเดต pip
python -m pip install --upgrade pip

# ติดตั้ง Dependencies
pip install -r requirements.txt

# ตรวจสอบ FFmpeg
ffmpeg -version