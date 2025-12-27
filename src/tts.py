"""Text-to-Speech module for natural voice output."""
import os
import tempfile
from openai import OpenAI
from typing import Optional
import simpleaudio as sa
import time
from src.config import TTS_MODEL


class TextToSpeech:
    """TTS module using OpenAI's text-to-speech API."""
    
    def __init__(self, api_key: str, voice: str = "alloy", speed: float = 1.0):
        """Initialize TTS.
        
        Args:
            api_key: OpenAI API key
            voice: Voice to use (alloy, echo, fable, onyx, nova, shimmer)
            speed: Speech speed (0.25 to 4.0)
        """
        self.client = OpenAI(api_key=api_key)
        self.voice = voice
        self.model = TTS_MODEL
        self.speed = speed
        self.is_speaking = False
    
    def speak(self, text: str, blocking: bool = True) -> None:
        """Convert text to speech and play it.
        
        Args:
            text: Text to speak
            blocking: If True, wait for speech to finish before returning
        """
        if not text.strip():
            return
        
        self.is_speaking = True
        
        try:
            # Generate speech
            response = self.client.audio.speech.create(
                model=self.model,
                voice=self.voice,
                input=text,
                speed=self.speed
            )
            
            # Save to temporary file
            with tempfile.NamedTemporaryFile(suffix='.mp3', delete=False) as tmp_file:
                tmp_file.write(response.content)
                tmp_path = tmp_file.name
            
            # Play audio
            try:
                # Convert MP3 to WAV for simpleaudio (requires pydub)
                from pydub import AudioSegment
                from pydub.playback import play
                
                audio = AudioSegment.from_mp3(tmp_path)
                if blocking:
                    play(audio)
                else:
                    # For non-blocking, we'd need threading
                    play(audio)
            
            except ImportError:
                # Fallback: use system command
                import subprocess
                import platform
                
                if platform.system() == "Darwin":  # macOS
                    subprocess.run(["afplay", tmp_path], check=True)
                elif platform.system() == "Linux":
                    subprocess.run(["mpg123", tmp_path], check=True)
                elif platform.system() == "Windows":
                    subprocess.run(["start", tmp_path], shell=True, check=True)
            
            finally:
                # Clean up
                try:
                    os.unlink(tmp_path)
                except:
                    pass
        
        except Exception as e:
            print(f"TTS error: {e}")
        
        finally:
            self.is_speaking = False
    
    def speak_async(self, text: str):
        """Speak asynchronously (non-blocking)."""
        import threading
        thread = threading.Thread(target=self.speak, args=(text, True))
        thread.daemon = True
        thread.start()
        return thread

