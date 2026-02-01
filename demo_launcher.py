"""Simple demo launcher UI for Voice Facilitator."""
import os
import sys
import subprocess
import tkinter as tk
from tkinter import ttk, messagebox


ROOT_DIR = os.path.dirname(os.path.abspath(__file__))


class DemoLauncher(tk.Tk):
    def __init__(self):
        super().__init__()
        self.title("Voice Facilitator Demo Launcher")
        self.geometry("520x360")
        self.resizable(False, False)

        self.processes = {}

        wrapper = ttk.Frame(self, padding=18)
        wrapper.pack(fill="both", expand=True)

        title = ttk.Label(wrapper, text="Voice Facilitator Demo", font=("Helvetica", 16, "bold"))
        title.pack(anchor="w")

        subtitle = ttk.Label(
            wrapper,
            text="Launch each experience from here. Each button starts a separate process.",
            foreground="#555555",
        )
        subtitle.pack(anchor="w", pady=(4, 14))

        btn_frame = ttk.Frame(wrapper)
        btn_frame.pack(fill="x")

        self._add_button(
            btn_frame,
            "Start Voice Bot",
            lambda: self._launch("voice_bot", [sys.executable, "main.py"]),
        )
        self._add_button(
            btn_frame,
            "Open Admin Dashboard",
            lambda: self._launch("dashboard", [sys.executable, "dashboard_dash.py"]),
        )
        self._add_button(
            btn_frame,
            "Admin Analytics (Voice)",
            lambda: self._launch("admin_analytics", [sys.executable, "analytics_admin.py", "--voice"]),
        )

        self.status = tk.StringVar(value="Ready.")
        status_label = ttk.Label(wrapper, textvariable=self.status, foreground="#2b2b2b")
        status_label.pack(anchor="w", pady=(16, 6))

        stop_btn = ttk.Button(wrapper, text="Stop All", command=self._stop_all)
        stop_btn.pack(anchor="w")

        note = ttk.Label(
            wrapper,
            text="Dashboard runs at http://127.0.0.1:8050 by default.",
            foreground="#666666",
        )
        note.pack(anchor="w", pady=(14, 0))

    def _add_button(self, parent, label, command):
        btn = ttk.Button(parent, text=label, command=command)
        btn.pack(fill="x", pady=6)

    def _launch(self, key, cmd):
        proc = self.processes.get(key)
        if proc and proc.poll() is None:
            messagebox.showinfo("Already running", f"{key.replace('_', ' ').title()} is already running.")
            return
        try:
            self.status.set(f"Launching {key.replace('_', ' ')}...")
            proc = subprocess.Popen(
                cmd,
                cwd=ROOT_DIR,
            )
            self.processes[key] = proc
            self.status.set(f"Running: {key.replace('_', ' ')}")
        except Exception as e:
            self.status.set("Ready.")
            messagebox.showerror("Launch failed", str(e))

    def _stop_all(self):
        stopped_any = False
        for key, proc in list(self.processes.items()):
            if proc and proc.poll() is None:
                try:
                    proc.terminate()
                    stopped_any = True
                except Exception:
                    pass
        self.status.set("Stopped all running processes." if stopped_any else "No active processes to stop.")


def main():
    app = DemoLauncher()
    app.mainloop()


if __name__ == "__main__":
    main()
