#!/usr/bin/env python3
"""
Fix Broken Footprints - Repair literal \\n characters in model paths

This fixes footprints that have literal "\\n" characters instead of actual newlines,
which makes them unreadable by KiCad.

Example of broken footprint:
    \\n  (model "..\\3d\\step\\B2B-XH-A.step")\\n)
    
Will be fixed to:
    
  (model "..\\3d\\step\\B2B-XH-A.step")
)
"""

import re
from pathlib import Path
import shutil
import tkinter as tk
from tkinter import ttk, filedialog, messagebox, scrolledtext
from datetime import datetime

COLORS = {
    'bg': '#1e1e1e',
    'panel': '#2d2d2d',
    'text': '#d4d4d4',
    'text_muted': '#808080',
    'accent': '#007acc',
    'success': '#4ec9b0',
    'warning': '#dcdcaa',
    'error': '#f48771',
}


class FootprintRepair(tk.Tk):
    def __init__(self):
        super().__init__()
        
        self.title("Fix Broken KiCad Footprints")
        self.geometry("900x600")
        self.configure(bg=COLORS['bg'])
        
        self.setup_styles()
        self.build_ui()
    
    def setup_styles(self):
        style = ttk.Style()
        style.theme_use('clam')
        
        style.configure(".", background=COLORS['bg'], foreground=COLORS['text'])
        style.configure("TLabel", background=COLORS['bg'], foreground=COLORS['text'])
        style.configure("TButton", background=COLORS['panel'], foreground=COLORS['text'])
        style.configure("Accent.TButton", background=COLORS['accent'], foreground='white')
        style.map("TButton", background=[('active', COLORS['accent'])])
        style.configure("TFrame", background=COLORS['bg'])
        style.configure("TLabelframe", background=COLORS['bg'], foreground=COLORS['text'])
        style.configure("TLabelframe.Label", background=COLORS['bg'], foreground=COLORS['text'])
    
    def build_ui(self):
        # Header
        header = ttk.Frame(self)
        header.pack(fill="x", padx=12, pady=12)
        
        ttk.Label(header, text="Fix Broken KiCad Footprints",
                 font=("Segoe UI", 16, "bold")).pack(side="left")
        
        ttk.Button(header, text="📁 Select Footprint Directory", 
                  command=self.select_directory,
                  style="Accent.TButton").pack(side="right", padx=4)
        
        # Info
        info_frame = ttk.Labelframe(self, text="Problem", padding=12)
        info_frame.pack(fill="x", padx=12, pady=(0,12))
        
        info_text = (
            "Some footprints have literal '\\n' characters instead of actual newlines.\n"
            "This makes them unreadable by KiCad.\n\n"
            "Example of broken text: \\n  (model \"path\")\\n)\n"
            "This tool will fix these automatically."
        )
        ttk.Label(info_frame, text=info_text, foreground=COLORS['text_muted']).pack(anchor="w")
        
        # Output
        output_frame = ttk.Labelframe(self, text="Scan Results", padding=12)
        output_frame.pack(fill="both", expand=True, padx=12, pady=(0,12))
        
        self.output = scrolledtext.ScrolledText(output_frame, wrap="word", 
                                                bg=COLORS['panel'], fg=COLORS['text'],
                                                font=("Consolas", 10))
        self.output.pack(fill="both", expand=True)
        
        # Status
        status_frame = ttk.Frame(self)
        status_frame.pack(fill="x", padx=12, pady=(0,12))
        
        self.status_label = ttk.Label(status_frame, text="Select a footprint directory to scan",
                                      foreground=COLORS['text_muted'])
        self.status_label.pack(side="left")
        
        ttk.Button(status_frame, text="🔧 Fix All Broken Files",
                  command=self.fix_files,
                  style="Accent.TButton").pack(side="right", padx=4)
    
    def log(self, message, level="INFO"):
        color_map = {
            "INFO": COLORS['text'],
            "SUCCESS": COLORS['success'],
            "WARNING": COLORS['warning'],
            "ERROR": COLORS['error']
        }
        
        self.output.insert("end", f"{message}\n")
        self.output.see("end")
        self.update()
    
    def select_directory(self):
        directory = filedialog.askdirectory(title="Select Footprint Directory")
        if not directory:
            return
        
        self.fp_dir = Path(directory)
        self.scan_directory()
    
    def scan_directory(self):
        self.output.delete("1.0", "end")
        self.broken_files = []
        
        self.log("=" * 70)
        self.log(f"Scanning: {self.fp_dir}")
        self.log("=" * 70)
        
        if not self.fp_dir.exists():
            self.log("ERROR: Directory not found!", "ERROR")
            return
        
        # Find all .kicad_mod files
        footprints = list(self.fp_dir.glob("*.kicad_mod"))
        
        if not footprints:
            self.log("No footprint files found!", "WARNING")
            return
        
        self.log(f"\nFound {len(footprints)} footprint files")
        self.log("\nScanning for broken files...\n")
        
        for fp in footprints:
            try:
                content = fp.read_text(encoding='utf-8')
                
                # Check for literal \n characters (not actual newlines)
                # Looking for patterns like: \n  (model or )\n)
                if r'\n' in content:
                    self.log(f"❌ BROKEN: {fp.name}", "ERROR")
                    self.broken_files.append(fp)
                    
                    # Show what's broken
                    matches = re.findall(r'\\n[^\\]*\\n', content)
                    if matches:
                        self.log(f"   Found: {matches[0][:50]}...", "ERROR")
                        
            except Exception as e:
                self.log(f"⚠️  ERROR reading {fp.name}: {e}", "WARNING")
        
        self.log("\n" + "=" * 70)
        
        if self.broken_files:
            self.log(f"\n✅ Scan complete: Found {len(self.broken_files)} broken files", "WARNING")
            self.status_label.config(
                text=f"Found {len(self.broken_files)} broken files - Click 'Fix All' to repair",
                foreground=COLORS['warning']
            )
        else:
            self.log("\n✅ Scan complete: All files OK!", "SUCCESS")
            self.status_label.config(
                text="All files are OK! No repairs needed.",
                foreground=COLORS['success']
            )
    
    def fix_files(self):
        if not hasattr(self, 'broken_files') or not self.broken_files:
            messagebox.showwarning("Nothing to Fix", "No broken files found. Scan a directory first.")
            return
        
        if not messagebox.askyesno("Confirm Repair",
            f"This will repair {len(self.broken_files)} broken footprint files.\n\n"
            "• Backups will be created (.bak)\n"
            "• Literal '\\n' will be replaced with actual newlines\n\n"
            "Continue?"):
            return
        
        self.log("\n" + "=" * 70)
        self.log("STARTING REPAIR")
        self.log("=" * 70 + "\n")
        
        fixed_count = 0
        error_count = 0
        
        for fp in self.broken_files:
            try:
                # Read file
                content = fp.read_text(encoding='utf-8')
                
                # Create backup
                backup_path = fp.with_suffix('.kicad_mod.bak')
                shutil.copy2(fp, backup_path)
                
                # Fix the content - replace literal \n with actual newlines
                # Be careful to only replace the escaped ones, not in strings
                fixed_content = content.replace(r'\n', '\n')
                
                # Write fixed content
                fp.write_text(fixed_content, encoding='utf-8')
                
                self.log(f"✅ FIXED: {fp.name}", "SUCCESS")
                self.log(f"   Backup: {backup_path.name}", "INFO")
                fixed_count += 1
                
            except Exception as e:
                self.log(f"❌ ERROR fixing {fp.name}: {e}", "ERROR")
                error_count += 1
        
        self.log("\n" + "=" * 70)
        self.log(f"REPAIR COMPLETE", "SUCCESS")
        self.log("=" * 70)
        self.log(f"\n✅ Fixed: {fixed_count} files")
        if error_count > 0:
            self.log(f"❌ Errors: {error_count} files", "ERROR")
        
        self.status_label.config(
            text=f"Repair complete! Fixed {fixed_count} files.",
            foreground=COLORS['success']
        )
        
        messagebox.showinfo("Repair Complete",
            f"Successfully repaired {fixed_count} footprint files!\n\n"
            f"Backups created: .bak files\n\n"
            f"Open KiCad to verify footprints load correctly.")


if __name__ == "__main__":
    app = FootprintRepair()
    app.mainloop()
