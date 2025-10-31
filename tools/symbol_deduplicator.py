#!/usr/bin/env python3
"""
KiCad Symbol Library Deduplicator
Cleans up duplicate symbols with _imp1, _imp2, _1_1 suffixes
"""

import re
import tkinter as tk
from tkinter import ttk, filedialog, messagebox, scrolledtext
from pathlib import Path
from collections import defaultdict
import shutil
from datetime import datetime

# Colors
COLORS = {
    'bg': '#1e1e1e',
    'panel': '#2d2d2d',
    'text': '#d4d4d4',
    'text_muted': '#808080',
    'accent': '#007acc',
    'success': '#4ec9b0',
    'warning': '#dcdcaa',
    'error': '#f48771',
    'border': '#3e3e3e'
}


class SymbolDeduplicator(tk.Tk):
    def __init__(self):
        super().__init__()
        
        self.title("KiCad Symbol Deduplicator")
        self.geometry("1200x800")
        self.configure(bg=COLORS['bg'])
        
        # Setup styles
        self.setup_styles()
        
        # Variables
        self.symbol_file = None
        self.duplicates = {}
        self.selections = {}  # Which version to keep for each group
        
        # Build UI
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
        
        # Treeview
        style.configure("Treeview",
            background=COLORS['panel'],
            foreground=COLORS['text'],
            fieldbackground=COLORS['panel'],
            borderwidth=0
        )
        style.configure("Treeview.Heading",
            background=COLORS['border'],
            foreground=COLORS['text'],
            relief="flat"
        )
        style.map("Treeview", background=[('selected', COLORS['accent'])])
    
    def build_ui(self):
        # Header
        header = ttk.Frame(self)
        header.pack(fill="x", padx=12, pady=12)
        
        ttk.Label(header, text="Symbol Library Deduplicator",
                 font=("Segoe UI", 16, "bold")).pack(side="left")
        
        ttk.Button(header, text="📁 Load Library", 
                  command=self.load_library,
                  style="Accent.TButton").pack(side="right", padx=4)
        
        # Info panel
        info_frame = ttk.Labelframe(self, text="Library Info", padding=12)
        info_frame.pack(fill="x", padx=12, pady=(0,12))
        
        self.info_label = ttk.Label(info_frame, text="No library loaded", 
                                    foreground=COLORS['text_muted'])
        self.info_label.pack(anchor="w")
        
        # Duplicate groups list
        list_frame = ttk.Labelframe(self, text="Duplicate Symbol Groups", padding=12)
        list_frame.pack(fill="both", expand=True, padx=12, pady=(0,12))
        
        # Tree
        cols = ("base_name", "count", "versions", "action")
        self.tree = ttk.Treeview(list_frame, columns=cols, show="headings", selectmode="browse")
        self.tree.pack(side="left", fill="both", expand=True)
        
        # Scrollbar
        vsb = ttk.Scrollbar(list_frame, orient="vertical", command=self.tree.yview)
        vsb.pack(side="right", fill="y")
        self.tree.configure(yscrollcommand=vsb.set)
        
        # Configure columns
        self.tree.heading("base_name", text="Symbol Base Name")
        self.tree.heading("count", text="# Versions")
        self.tree.heading("versions", text="All Versions")
        self.tree.heading("action", text="Will Keep")
        
        self.tree.column("base_name", width=250, anchor="w")
        self.tree.column("count", width=80, anchor="center")
        self.tree.column("versions", width=500, anchor="w")
        self.tree.column("action", width=250, anchor="w")
        
        self.tree.bind("<Double-1>", self.on_double_click)
        
        # Action buttons
        btn_frame = ttk.Frame(self)
        btn_frame.pack(fill="x", padx=12, pady=(0,12))
        
        self.status_label = ttk.Label(btn_frame, text="", foreground=COLORS['text_muted'])
        self.status_label.pack(side="left", padx=8)
        
        ttk.Button(btn_frame, text="Auto-Select Newest", 
                  command=self.auto_select_newest).pack(side="right", padx=4)
        ttk.Button(btn_frame, text="🗑️ Clean Library", 
                  command=self.clean_library,
                  style="Accent.TButton").pack(side="right", padx=4)
    
    def load_library(self):
        path = filedialog.askopenfilename(
            title="Select Symbol Library",
            filetypes=[("KiCad Symbol Library", "*.kicad_sym"), ("All Files", "*.*")]
        )
        
        if not path:
            return
        
        self.symbol_file = Path(path)
        self.analyze_library()
    
    def analyze_library(self):
        """Analyze library for duplicates."""
        try:
            content = self.symbol_file.read_text(encoding='utf-8')
        except Exception as e:
            messagebox.showerror("Error", f"Failed to read library:\n{e}")
            return
        
        # Find all symbols
        symbol_pattern = r'\(symbol "([^"]+)"'
        symbols = re.findall(symbol_pattern, content)
        
        # Group by base name
        base_groups = defaultdict(list)
        for sym in symbols:
            base = self.get_base_name(sym)
            base_groups[base].append(sym)
        
        # Find duplicates
        self.duplicates = {k: v for k, v in base_groups.items() if len(v) > 1}
        
        # Update UI
        total_symbols = len(symbols)
        unique_bases = len(base_groups)
        duplicate_count = len(symbols) - len(base_groups)
        
        self.info_label.config(
            text=f"📁 {self.symbol_file.name} | "
                 f"Total: {total_symbols} symbols | "
                 f"Unique: {unique_bases} | "
                 f"Duplicates: {duplicate_count}",
            foreground=COLORS['warning'] if duplicate_count > 0 else COLORS['success']
        )
        
        self.populate_tree()
        self.status_label.config(text=f"Found {len(self.duplicates)} symbol groups with duplicates")
    
    def get_base_name(self, symbol_name):
        """Extract base name from symbol (remove _imp1, _1_1, etc.)"""
        base = symbol_name
        base = re.sub(r'_imp\d+$', '', base)
        base = re.sub(r'_\d+_\d+$', '', base)
        return base
    
    def populate_tree(self):
        """Populate tree with duplicate groups."""
        # Clear tree
        for item in self.tree.get_children():
            self.tree.delete(item)
        
        self.selections.clear()
        
        # Add groups
        for base, versions in sorted(self.duplicates.items()):
            # Default: keep the base name if it exists, otherwise the first
            if base in versions:
                default_keep = base
            else:
                default_keep = versions[0]
            
            self.selections[base] = default_keep
            
            self.tree.insert("", "end", values=(
                base,
                len(versions),
                ", ".join(versions),
                f"✅ {default_keep}"
            ))
    
    def on_double_click(self, event):
        """Let user choose which version to keep."""
        item = self.tree.identify_row(event.y)
        if not item:
            return
        
        values = self.tree.item(item, "values")
        base_name = values[0]
        versions = self.duplicates[base_name]
        
        # Create selection dialog
        dialog = tk.Toplevel(self)
        dialog.title(f"Select Version to Keep: {base_name}")
        dialog.geometry("600x400")
        dialog.configure(bg=COLORS['bg'])
        dialog.transient(self)
        dialog.grab_set()
        
        ttk.Label(dialog, text=f"Which version of '{base_name}' should we keep?",
                 font=("Segoe UI", 12)).pack(pady=12)
        
        ttk.Label(dialog, text="All others will be deleted:",
                 foreground=COLORS['text_muted']).pack(pady=(0,12))
        
        # Radio buttons
        selected = tk.StringVar(value=self.selections[base_name])
        
        frame = ttk.Frame(dialog)
        frame.pack(fill="both", expand=True, padx=20, pady=12)
        
        for version in versions:
            rb = ttk.Radiobutton(frame, text=version, variable=selected, value=version)
            rb.pack(anchor="w", pady=4)
        
        def confirm():
            self.selections[base_name] = selected.get()
            # Update tree
            self.tree.item(item, values=(
                base_name,
                len(versions),
                ", ".join(versions),
                f"✅ {selected.get()}"
            ))
            dialog.destroy()
        
        ttk.Button(dialog, text="✅ Confirm", command=confirm,
                  style="Accent.TButton").pack(pady=12)
    
    def auto_select_newest(self):
        """Auto-select the newest version (highest _imp number or base name)."""
        for base, versions in self.duplicates.items():
            # Prefer: base name > highest _imp > highest _1_1
            if base in versions:
                best = base
            else:
                # Sort by _imp number, then _1_1 pattern
                def sort_key(v):
                    imp_match = re.search(r'_imp(\d+)$', v)
                    if imp_match:
                        return (2, int(imp_match.group(1)))
                    num_match = re.search(r'_(\d+)_(\d+)$', v)
                    if num_match:
                        return (1, int(num_match.group(1)), int(num_match.group(2)))
                    return (0, 0)
                
                best = max(versions, key=sort_key)
            
            self.selections[base] = best
        
        self.populate_tree()
        messagebox.showinfo("Auto-Select", 
                           f"Auto-selected newest version for {len(self.duplicates)} groups")
    
    def clean_library(self):
        """Remove duplicate symbols, keeping only selected versions."""
        if not self.symbol_file or not self.duplicates:
            messagebox.showwarning("Nothing to Clean", "No duplicates found or library not loaded")
            return
        
        # Confirmation
        total_to_delete = sum(len(versions) - 1 for versions in self.duplicates.values())
        
        if not messagebox.askyesno("Confirm Cleanup",
            f"This will:\n"
            f"• Keep {len(self.duplicates)} selected symbols\n"
            f"• Delete {total_to_delete} duplicate symbols\n"
            f"• Create a backup (.bak)\n\n"
            f"Continue?"):
            return
        
        try:
            # Read library
            content = self.symbol_file.read_text(encoding='utf-8')
            
            # Create backup
            backup_path = self.symbol_file.with_suffix('.kicad_sym.bak')
            shutil.copy2(self.symbol_file, backup_path)
            
            # Build list of symbols to delete
            to_delete = set()
            for base, versions in self.duplicates.items():
                keep = self.selections[base]
                to_delete.update(v for v in versions if v != keep)
            
            # Remove symbols
            deleted_count = 0
            for symbol_name in to_delete:
                # Find symbol block
                pattern = rf'\(symbol "{re.escape(symbol_name)}".*?\n  \)\n(?=  \(symbol |\)\n$)'
                match = re.search(pattern, content, re.DOTALL)
                if match:
                    content = content[:match.start()] + content[match.end():]
                    deleted_count += 1
            
            # Write cleaned library
            self.symbol_file.write_text(content, encoding='utf-8')
            
            # Show results
            messagebox.showinfo("Success!",
                f"Library cleaned successfully!\n\n"
                f"✅ Deleted: {deleted_count} symbols\n"
                f"✅ Kept: {len(self.duplicates)} symbols\n"
                f"✅ Backup: {backup_path.name}\n\n"
                f"Reload library to verify.")
            
            # Reload
            self.analyze_library()
            
        except Exception as e:
            messagebox.showerror("Error", f"Cleanup failed:\n{e}")


if __name__ == "__main__":
    app = SymbolDeduplicator()
    app.mainloop()
