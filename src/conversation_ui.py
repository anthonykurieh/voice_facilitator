"""Lightweight chat-style UI for live voice conversations."""
from __future__ import annotations

from datetime import datetime
from typing import Dict

try:
    import tkinter as tk
    from tkinter import ttk
except Exception:  # pragma: no cover
    tk = None
    ttk = None


class ConversationUI:
    """Simple chat transcript window that can be updated from voice loops."""

    def __init__(self, title: str, subtitle: str, role_labels: Dict[str, str]):
        self.role_labels = role_labels
        self._active = False
        self._root = None
        self._messages_canvas = None
        self._messages_frame = None
        self._canvas_window_id = None
        self._status_var = None

        if tk is None or ttk is None:
            return

        try:
            self._root = tk.Tk()
            self._root.title(title)
            self._root.geometry("560x720")
            self._root.minsize(460, 560)
            self._root.configure(bg="#0b1220")

            header = tk.Frame(self._root, bg="#101827", padx=18, pady=16)
            header.pack(fill="x", padx=12, pady=(12, 8))
            tk.Label(
                header,
                text=title,
                bg="#101827",
                fg="#f1f5f9",
                font=("Avenir Next", 18, "bold"),
            ).pack(anchor="w")
            tk.Label(
                header,
                text=subtitle,
                bg="#101827",
                fg="#94a3b8",
                font=("Avenir Next", 10),
            ).pack(anchor="w", pady=(6, 0))
            tk.Label(
                header,
                text="VOICE TRANSCRIPT",
                bg="#101827",
                fg="#38bdf8",
                font=("Avenir Next", 9, "bold"),
                padx=8,
                pady=3,
            ).pack(anchor="w", pady=(10, 0))

            body_outer = tk.Frame(self._root, bg="#0b1220", padx=12, pady=0)
            body_outer.pack(fill="both", expand=True)
            body = tk.Frame(body_outer, bg="#0f172a", padx=10, pady=10)
            body.pack(fill="both", expand=True)

            self._messages_canvas = tk.Canvas(
                body,
                bg="#0f172a",
                highlightthickness=0,
                bd=0,
            )
            scrollbar = ttk.Scrollbar(body, orient="vertical", command=self._messages_canvas.yview)
            self._messages_canvas.configure(yscrollcommand=scrollbar.set)

            self._messages_canvas.pack(side="left", fill="both", expand=True)
            scrollbar.pack(side="right", fill="y")

            self._messages_frame = tk.Frame(self._messages_canvas, bg="#0f172a")
            self._canvas_window_id = self._messages_canvas.create_window((0, 0), window=self._messages_frame, anchor="nw")
            self._messages_frame.bind("<Configure>", self._on_frame_configure)
            self._messages_canvas.bind("<Configure>", self._on_canvas_resize)

            self._add_welcome_card()

            footer = tk.Frame(self._root, bg="#0b1220", padx=12, pady=12)
            footer.pack(fill="x", padx=12, pady=(8, 12))
            footer_card = tk.Frame(footer, bg="#111b2f", padx=10, pady=8)
            footer_card.pack(fill="x")
            self._status_var = tk.StringVar(value="Status: Listening for conversation...")
            tk.Label(
                footer_card,
                textvariable=self._status_var,
                bg="#111b2f",
                fg="#93c5fd",
                font=("Avenir Next", 10, "bold"),
            ).pack(anchor="w")

            self._root.protocol("WM_DELETE_WINDOW", self._on_close)
            self._active = True
            self.pump()
        except Exception:
            self._active = False
            self._root = None

    @property
    def active(self) -> bool:
        return self._active and self._root is not None

    def _on_close(self):
        self._active = False
        try:
            if self._root is not None:
                self._root.destroy()
        except Exception:
            pass
        self._root = None

    def _on_frame_configure(self, _event=None):
        if self._messages_canvas is not None:
            self._messages_canvas.configure(scrollregion=self._messages_canvas.bbox("all"))

    def _on_canvas_resize(self, event=None):
        if not self._messages_canvas or not self._messages_frame or not event or self._canvas_window_id is None:
            return
        width = max(event.width - 10, 100)
        self._messages_canvas.itemconfig(self._canvas_window_id, width=width)

    def _add_welcome_card(self):
        if not self._messages_frame:
            return
        card = tk.Frame(self._messages_frame, bg="#17243c", padx=12, pady=10)
        card.pack(fill="x", padx=2, pady=(2, 8))
        tk.Label(
            card,
            text="Live voice conversation is running.",
            bg="#17243c",
            fg="#e2e8f0",
            font=("Avenir Next", 10, "bold"),
        ).pack(anchor="w")
        tk.Label(
            card,
            text="Customer/admin speech and agent replies will appear here in real time.",
            bg="#17243c",
            fg="#a5b4c8",
            font=("Avenir Next", 9),
            wraplength=460,
            justify="left",
        ).pack(anchor="w", pady=(4, 0))

    def pump(self):
        """Process pending UI events without blocking."""
        if not self.active:
            return
        try:
            self._root.update_idletasks()
            self._root.update()
        except Exception:
            self._active = False

    def set_status(self, text: str):
        if not self.active:
            return
        if self._status_var is not None:
            self._status_var.set(f"Status: {text}")
        self.pump()

    def add_turn(self, role: str, text: str):
        if not self.active or not text:
            return
        label = self.role_labels.get(role, role.title())
        ts = datetime.now().strftime("%I:%M %p").lstrip("0")

        if role == "user":
            bubble_bg = "#0b2f4a"
            bubble_fg = "#e0f2fe"
            meta_fg = "#7dd3fc"
            outer_anchor = "w"
            pad_left, pad_right = 4, 70
        elif role == "assistant":
            bubble_bg = "#1f2937"
            bubble_fg = "#f8fafc"
            meta_fg = "#c4b5fd"
            outer_anchor = "e"
            pad_left, pad_right = 70, 4
        else:
            bubble_bg = "#273449"
            bubble_fg = "#e2e8f0"
            meta_fg = "#94a3b8"
            outer_anchor = "w"
            pad_left, pad_right = 4, 4

        row = tk.Frame(self._messages_frame, bg="#0f172a")
        row.pack(fill="x", pady=6, padx=2)

        meta = tk.Label(
            row,
            text=f"{label}  {ts}",
            bg="#0f172a",
            fg=meta_fg,
            font=("Avenir Next", 9, "bold"),
        )
        meta.pack(anchor=outer_anchor, padx=(pad_left, pad_right))

        bubble = tk.Label(
            row,
            text=text,
            justify="left",
            wraplength=400,
            bg=bubble_bg,
            fg=bubble_fg,
            padx=14,
            pady=11,
            font=("Avenir Next", 11),
        )
        bubble.pack(anchor=outer_anchor, padx=(pad_left, pad_right), pady=(2, 0))

        self._on_frame_configure()
        if self._messages_canvas is not None:
            self._messages_canvas.yview_moveto(1.0)
        self.pump()

    def close(self):
        self._on_close()
