import uvicorn
import webbrowser
import threading
import os

def open_browser():
    webbrowser.open("http://127.0.0.1:8000")

if __name__ == "__main__":
    # Start a thread to open the web browser
    threading.Timer(1.5, open_browser).start()
    
    # Run the FastAPI application using Uvicorn
    # The import matches app/backend/main.py -> app
    uvicorn.run("backend.main:app", host="127.0.0.1", port=8000, reload=False)
