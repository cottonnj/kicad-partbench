#!/usr/bin/env python3
"""
Repair Broken Symbols - Fix MPN properties incorrectly nested inside other properties

This repairs symbols where MPN property was inserted INSIDE another property,
breaking the S-expression structure.

Example of broken:
    (property "Reference" "J"
      (at -12.7 13.462 0)
        (property "MPN" "12345")  ← WRONG! Inside Reference property
      (effects ...

Should be:
    (property "Reference" "J"
      (at -12.7 13.462 0)
      (effects ...
    )
    (property "MPN" "12345"  ← Correct! Separate property
      (at 0 0 0)
      (effects ...
    )
"""

import re
from pathlib import Path
import shutil
import tkinter as tk
from tkinter import ttk, filedialog, messagebox, scrolledtext

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


class SymbolRepair(tk.Tk):
    def __init__(self):
        super().__init__()
        
        self.title("Repair Broken Symbol File")
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
        
        ttk.Label(header, text="Repair Broken Symbol File",
                 font=("Segoe UI", 16, "bold")).pack(side="left")
        
        ttk.Button(header, text="📁 Select Symbol File", 
                  command=self.select_file,
                  style="Accent.TButton").pack(side="right", padx=4)
        
        # Info
        info_frame = ttk.Labelframe(self, text="Problem", padding=12)
        info_frame.pack(fill="x", padx=12, pady=(0,12))
        
        info_text = (
            "Fixes MPN properties incorrectly nested INSIDE other properties.\n\n"
            "Broken structure:\n"
            "  (property \"Reference\" \"J\"\n"
            "    (at ...)\n"
            "      (property \"MPN\" \"12345\")  ← Wrong! Inside Reference\n\n"
            "This tool extracts and re-inserts them correctly."
        )
        ttk.Label(info_frame, text=info_text, foreground=COLORS['text_muted']).pack(anchor="w")
        
        # Output
        output_frame = ttk.Labelframe(self, text="Repair Results", padding=12)
        output_frame.pack(fill="both", expand=True, padx=12, pady=(0,12))
        
        self.output = scrolledtext.ScrolledText(output_frame, wrap="word", 
                                                bg=COLORS['panel'], fg=COLORS['text'],
                                                font=("Consolas", 10))
        self.output.pack(fill="both", expand=True)
        
        # Status
        status_frame = ttk.Frame(self)
        status_frame.pack(fill="x", padx=12, pady=(0,12))
        
        self.status_label = ttk.Label(status_frame, text="Select a symbol file to repair",
                                      foreground=COLORS['text_muted'])
        self.status_label.pack(side="left")
        
        ttk.Button(status_frame, text="🔧 Repair File",
                  command=self.repair_file,
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
    
    def select_file(self):
        path = filedialog.askopenfilename(
            title="Select Symbol Library File",
            filetypes=[("KiCad Symbol Library", "*.kicad_sym"), ("All Files", "*.*")]
        )
        
        if not path:
            return
        
        self.sym_file = Path(path)
        self.scan_file()
    
    def scan_file(self):
        self.output.delete("1.0", "end")
        
        self.log("=" * 70)
        self.log(f"Scanning: {self.sym_file.name}")
        self.log("=" * 70)
        
        if not self.sym_file.exists():
            self.log("ERROR: File not found!", "ERROR")
            return
        
        try:
            content = self.sym_file.read_text(encoding='utf-8')
        except Exception as e:
            self.log(f"ERROR: Failed to read file: {e}", "ERROR")
            return
        
        # Find nested MPN properties
        # Pattern: property inside another property (wrong indentation/nesting)
        nested_mpn_pattern = r'(\(property\s+"(?!MPN)[^"]+".+?)\s+(\(property\s+"MPN"\s+"[^"]+"\))'
        
        matches = list(re.finditer(nested_mpn_pattern, content, re.DOTALL))
        
        if matches:
            self.log(f"\n❌ Found {len(matches)} nested MPN properties", "ERROR")
            self.log("\nThese will be extracted and re-inserted correctly.", "INFO")
            self.broken_count = len(matches)
            self.status_label.config(
                text=f"Found {len(matches)} broken properties - Click 'Repair' to fix",
                foreground=COLORS['warning']
            )
        else:
            self.log("\n✅ No nested MPN properties found - file looks OK!", "SUCCESS")
            self.broken_count = 0
            self.status_label.config(
                text="File looks OK - no repairs needed",
                foreground=COLORS['success']
            )
    
    def repair_file(self):
        if not hasattr(self, 'sym_file') or not self.sym_file.exists():
            messagebox.showwarning("No File", "Please select a symbol file first.")
            return
        
        if not hasattr(self, 'broken_count') or self.broken_count == 0:
            messagebox.showinfo("Nothing to Fix", "File has no broken MPN properties.")
            return
        
        if not messagebox.askyesno("Confirm Repair",
            f"This will repair nested MPN properties.\n\n"
            "• Backup will be created (.bak)\n"
            "• MPN properties will be extracted and re-inserted\n\n"
            "Continue?"):
            return
        
        self.log("\n" + "=" * 70)
        self.log("STARTING REPAIR")
        self.log("=" * 70 + "\n")
        
        try:
            # Read file
            content = self.sym_file.read_text(encoding='utf-8')
            
            # Create backup
            backup_path = self.sym_file.with_suffix('.kicad_sym.bak')
            shutil.copy2(self.sym_file, backup_path)
            self.log(f"✅ Created backup: {backup_path.name}", "SUCCESS")
            
            # Extract all MPN properties and their values
            mpn_values = {}
            
            # Parse symbols and fix nesting
            # Split into symbols
            symbol_pattern = r'(\(symbol\s+"([^"]+)".*?)(?=\n  \(symbol\s+|\n\)$)'
            symbols = list(re.finditer(symbol_pattern, content, re.DOTALL))
            
            fixed_count = 0
            fixed_content = content[:content.find('(symbol')]  # Keep header
            
            for match in symbols:
                symbol_block = match.group(1)
                symbol_name = match.group(2)
                
                # Check for nested MPN
                nested_mpn = re.search(r'\s+(\(property\s+"MPN"\s+"([^"]+)"\))', symbol_block)
                
                if nested_mpn:
                    mpn_full = nested_mpn.group(1)
                    mpn_value = nested_mpn.group(2)
                    
                    # Remove the incorrectly placed MPN
                    symbol_block = symbol_block.replace(mpn_full, '')
                    
                    # Find where to insert properly (after Reference property)
                    ref_start = symbol_block.find('(property "Reference"')
                    
                    if ref_start != -1:
                        # Count parentheses to find end of Reference property
                        paren_count = 0
                        pos = ref_start
                        ref_end = -1
                        
                        while pos < len(symbol_block):
                            if symbol_block[pos] == '(':
                                paren_count += 1
                            elif symbol_block[pos] == ')':
                                paren_count -= 1
                                if paren_count == 0:
                                    ref_end = pos + 1
                                    break
                            pos += 1
                        
                        if ref_end != -1:
                            # Insert properly formatted MPN after Reference
                            proper_mpn = f'\n  (property "MPN" "{mpn_value}"\n    (at 0 0 0)\n    (effects\n      (font\n        (size 1.27 1.27)\n      )\n      (hide yes)\n    )\n  )'
                            
                            symbol_block = symbol_block[:ref_end] + proper_mpn + symbol_block[ref_end:]
                            
                            fixed_count += 1
                            self.log(f"✅ Fixed: {symbol_name}", "SUCCESS")
                
                fixed_content += symbol_block + "\n"
            
            # Close the symbol library
            fixed_content += ")\n"
            
            # Write repaired file
            self.sym_file.write_text(fixed_content, encoding='utf-8')
            
            self.log("\n" + "=" * 70)
            self.log(f"REPAIR COMPLETE", "SUCCESS")
            self.log("=" * 70)
            self.log(f"\n✅ Fixed {fixed_count} symbols")
            self.log(f"✅ Backup: {backup_path.name}")
            self.log("\n💡 Open the file in KiCad to verify it loads correctly!")
            
            self.status_label.config(
                text=f"Repair complete! Fixed {fixed_count} symbols.",
                foreground=COLORS['success']
            )
            
            messagebox.showinfo("Repair Complete",
                f"Successfully repaired {fixed_count} symbols!\n\n"
                f"Backup: {backup_path.name}\n\n"
                f"Open in KiCad to verify.")
            
        except Exception as e:
            self.log(f"\n❌ ERROR: {e}", "ERROR")
            messagebox.showerror("Error", f"Repair failed:\n{e}")


if __name__ == "__main__":
    app = SymbolRepair()
    app.mainloop()
