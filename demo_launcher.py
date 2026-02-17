"""Simple demo launcher UI for Voice Facilitator."""
import os
import sys
import subprocess
import webbrowser
import tkinter as tk
from tkinter import ttk, messagebox
from pathlib import Path
import yaml


ROOT_DIR = os.path.dirname(os.path.abspath(__file__))
CONFIG_DIR = Path(ROOT_DIR) / "config"


class DemoLauncher(tk.Tk):
    def __init__(self):
        super().__init__()
        self.title("Voice Facilitator Control Center")
        self.geometry("980x760")
        self.minsize(900, 700)
        self.resizable(True, True)

        self.processes = {}
        self.config_map = {}
        self._config_snapshot = tuple()
        self._configure_styles()

        wrapper = ttk.Frame(self, padding=20, style="App.TFrame")
        wrapper.pack(fill="both", expand=True)

        hero = ttk.Frame(wrapper, style="Hero.TFrame", padding=18)
        hero.pack(fill="x")

        title = ttk.Label(hero, text="Voice Facilitator", style="HeroTitle.TLabel")
        title.pack(anchor="w")

        subtitle = ttk.Label(
            hero,
            text="Run customer, analytics, dashboard, config builder, and daily email workflows from one place.",
            style="HeroSub.TLabel",
        )
        subtitle.pack(anchor="w", pady=(6, 0))

        content = ttk.Frame(wrapper, style="App.TFrame")
        content.pack(fill="both", expand=True, pady=(14, 0))

        config_card = ttk.LabelFrame(content, text="Business Profile", padding=14, style="Section.TLabelframe")
        config_card.pack(fill="x", pady=(0, 12))

        self.config_var = tk.StringVar()
        self.config_combo = ttk.Combobox(
            config_card,
            textvariable=self.config_var,
            state="readonly",
            style="Large.TCombobox",
        )
        self.config_combo.pack(fill="x")
        self.config_combo.bind("<<ComboboxSelected>>", self._on_config_selected)

        config_actions = ttk.Frame(config_card)
        config_actions.pack(fill="x", pady=(8, 0))
        ttk.Button(config_actions, text="Refresh Configs", style="Secondary.TButton", command=self._load_config_options).pack(side="left")

        self.business_preview = tk.StringVar(value="No business config selected.")
        ttk.Label(config_card, textvariable=self.business_preview, style="Meta.TLabel", wraplength=900, justify="left").pack(anchor="w", pady=(10, 0))

        self.mode_frame = ttk.LabelFrame(content, text="Mode Selection", padding=14, style="Section.TLabelframe")
        self.mode_frame.pack(fill="x")

        self._add_button(
            self.mode_frame,
            "Customer Mode",
            self._launch_customer_mode,
        )
        self._add_button(
            self.mode_frame,
            "Admin Mode",
            self._show_admin_mode,
        )

        self.admin_frame = ttk.LabelFrame(content, text="Admin Tools", padding=14, style="Section.TLabelframe")
        self._build_admin_buttons()

        footer = ttk.Frame(content, style="Footer.TFrame", padding=12)
        footer.pack(fill="x", pady=(14, 0))
        self.status = tk.StringVar(value="Ready")
        status_label = ttk.Label(footer, textvariable=self.status, style="Status.TLabel")
        status_label.pack(anchor="w")

        actions = ttk.Frame(footer, style="Footer.TFrame")
        actions.pack(fill="x", pady=(8, 0))
        stop_btn = ttk.Button(actions, text="Stop All Running Processes", style="Danger.TButton", command=self._stop_all)
        stop_btn.pack(side="left")

        note = ttk.Label(
            actions,
            text="Dashboard runs at http://127.0.0.1:8050 by default.",
            style="Meta.TLabel",
        )
        note.pack(side="left", padx=(14, 0))

        self._load_config_options()
        self.after(2000, self._auto_refresh_configs)

    def _configure_styles(self):
        style = ttk.Style(self)
        try:
            style.theme_use("clam")
        except Exception:
            pass
        style.configure("App.TFrame", background="#eef3fb")
        style.configure("Hero.TFrame", background="#0f1f3d")
        style.configure("HeroTitle.TLabel", background="#0f1f3d", foreground="#f8fafc", font=("Helvetica", 24, "bold"))
        style.configure("HeroSub.TLabel", background="#0f1f3d", foreground="#cdd8ef", font=("Helvetica", 11))
        style.configure("Section.TLabelframe", background="#f6f9ff", borderwidth=1, relief="solid")
        style.configure("Section.TLabelframe.Label", background="#f6f9ff", foreground="#1e3a5f", font=("Helvetica", 12, "bold"))
        style.configure("Meta.TLabel", background="#f6f9ff", foreground="#4b5d78", font=("Helvetica", 10))
        style.configure("Status.TLabel", background="#e8eef8", foreground="#1f2a3d", font=("Helvetica", 11, "bold"))
        style.configure("Footer.TFrame", background="#e8eef8")
        style.configure("Primary.TButton", font=("Helvetica", 11, "bold"), padding=(12, 10))
        style.configure("Secondary.TButton", font=("Helvetica", 10), padding=(10, 8))
        style.configure("Danger.TButton", font=("Helvetica", 10, "bold"), padding=(12, 10))
        style.configure("Large.TCombobox", padding=6)

    def _build_admin_buttons(self):
        self._add_button(
            self.admin_frame,
            "Open Admin Dashboard",
            lambda: self._launch("dashboard", [sys.executable, "dashboard_dash.py"]),
        )
        self._add_button(
            self.admin_frame,
            "Business Config Builder",
            self._launch_business_builder,
        )
        self._add_button(
            self.admin_frame,
            "Conversational Analytics (Admin)",
            lambda: self._launch("admin_analytics", [sys.executable, "analytics_admin.py", "--voice"]),
        )
        self._add_button(
            self.admin_frame,
            "Send Daily Staff Emails",
            self._send_daily_staff_emails,
        )
        self._add_button(
            self.admin_frame,
            "Back",
            self._show_mode_select,
        )

    def _show_admin_mode(self):
        self.mode_frame.pack_forget()
        self.admin_frame.pack(fill="x")

    def _show_mode_select(self):
        self.admin_frame.pack_forget()
        self.mode_frame.pack(fill="x")

    def _add_button(self, parent, label, command):
        btn = ttk.Button(parent, text=label, command=command, style="Primary.TButton")
        btn.pack(fill="x", pady=7)

    def _load_config_options(self):
        self.config_map = {}
        config_paths = sorted(CONFIG_DIR.glob("business_config*.yaml"))
        self._config_snapshot = tuple(str(p) for p in config_paths)
        options = []
        for path in config_paths:
            label = self._format_config_label(path)
            self.config_map[label] = str(path.relative_to(ROOT_DIR))
            options.append(label)
        self.config_combo["values"] = options
        if not options:
            self.config_var.set("")
            self.business_preview.set("No config files found in ./config.")
            return
        if self.config_var.get() not in options:
            self.config_var.set(options[0])
        self._on_config_selected()

    def _auto_refresh_configs(self):
        """Keep config dropdown in sync with files created by the builder."""
        try:
            current_paths = tuple(str(p) for p in sorted(CONFIG_DIR.glob("business_config*.yaml")))
            if current_paths != self._config_snapshot:
                previous_value = self.config_var.get()
                self._load_config_options()
                # Keep previous selection if still available; otherwise leave loader default.
                if previous_value in self.config_map:
                    self.config_var.set(previous_value)
                    self._on_config_selected()
                self.status.set("Detected new/updated config files.")
        finally:
            self.after(2000, self._auto_refresh_configs)

    def _format_config_label(self, path: Path) -> str:
        try:
            with path.open("r", encoding="utf-8") as f:
                data = yaml.safe_load(f) or {}
            business = data.get("business", {}) if isinstance(data, dict) else {}
            name = business.get("name") or path.stem
            biz_type = business.get("type") or "business"
            return f"{name} ({biz_type})"
        except Exception:
            return f"{path.stem} (unreadable)"

    def _on_config_selected(self, _event=None):
        selected = self.config_var.get()
        rel_path = self.config_map.get(selected)
        if not rel_path:
            self.business_preview.set("No business config selected.")
            return
        cfg_path = Path(ROOT_DIR) / rel_path
        try:
            with cfg_path.open("r", encoding="utf-8") as f:
                data = yaml.safe_load(f) or {}
            business = data.get("business", {})
            services = data.get("services", []) if isinstance(data, dict) else []
            staff = data.get("staff", []) if isinstance(data, dict) else []
            preview = (
                f"Config: {rel_path} | "
                f"Business: {business.get('name', 'Unknown')} | "
                f"Timezone: {business.get('timezone', 'N/A')} | "
                f"Services: {len(services)} | Staff: {len(staff)}"
            )
            self.business_preview.set(preview)
        except Exception as e:
            self.business_preview.set(f"Config preview unavailable: {e}")

    def _launch_customer_mode(self):
        selected = self.config_var.get()
        rel_path = self.config_map.get(selected)
        if not rel_path:
            messagebox.showerror("Missing config", "Please select a business config first.")
            return
        env = os.environ.copy()
        env["CONFIG_FILE"] = rel_path
        self._launch("voice_bot", [sys.executable, "main.py"], env=env)

    def _launch_business_builder(self):
        self._launch(
            "business_builder",
            [sys.executable, "business_builder_server.py"],
        )
        try:
            webbrowser.open("http://127.0.0.1:8765")
        except Exception:
            pass

    def _send_daily_staff_emails(self):
        selected = self.config_var.get()
        rel_path = self.config_map.get(selected)
        if not rel_path:
            messagebox.showerror("Missing config", "Please select a business config first.")
            return

        base_env = os.environ.copy()
        base_env["CONFIG_FILE"] = rel_path
        self.status.set(f"Preparing daily staff emails using {rel_path}...")
        try:
            # Integrated dry-run validation (no send yet).
            dry_env = dict(base_env)
            dry_env["DRY_RUN"] = "true"
            dry_result = subprocess.run(
                [sys.executable, "send_daily_staff_emails.py"],
                cwd=ROOT_DIR,
                env=dry_env,
                capture_output=True,
                text=True,
                timeout=120,
            )
            dry_output = (dry_result.stdout or "").strip()
            dry_err = (dry_result.stderr or "").strip()
            if dry_result.returncode != 0:
                self.status.set("Email pre-check failed.")
                messagebox.showerror("Daily Emails Failed", (dry_err or dry_output or "Unknown error"))
                return

            preview = dry_output or "Dry run completed."
            should_send = messagebox.askyesno(
                "Confirm Send",
                f"Pre-check completed:\n\n{preview}\n\nSend real emails now?"
            )
            if not should_send:
                self.status.set("Email send cancelled after preview.")
                return

            self.status.set("Sending daily staff emails...")
            send_result = subprocess.run(
                [sys.executable, "send_daily_staff_emails.py"],
                cwd=ROOT_DIR,
                env=base_env,
                capture_output=True,
                text=True,
                timeout=120,
            )
            output = (send_result.stdout or "").strip()
            err = (send_result.stderr or "").strip()
            if send_result.returncode == 0:
                self.status.set("Daily staff emails sent.")
                messagebox.showinfo("Daily Emails", output or "Daily staff emails completed successfully.")
            else:
                self.status.set("Email send failed.")
                messagebox.showerror("Daily Emails Failed", (err or output or "Unknown error"))
        except Exception as e:
            self.status.set("Email send failed.")
            messagebox.showerror("Daily Emails Failed", str(e))

    def _launch(self, key, cmd, env=None):
        proc = self.processes.get(key)
        if proc and proc.poll() is None:
            messagebox.showinfo("Already running", f"{key.replace('_', ' ').title()} is already running.")
            return
        try:
            self.status.set(f"Launching {key.replace('_', ' ')}...")
            proc = subprocess.Popen(
                cmd,
                cwd=ROOT_DIR,
                env=env or os.environ.copy(),
            )
            self.processes[key] = proc
            if key == "voice_bot" and env and env.get("CONFIG_FILE"):
                self.status.set(f"Running: {key.replace('_', ' ')} ({env['CONFIG_FILE']})")
            else:
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
