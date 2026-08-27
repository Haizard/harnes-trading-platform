"""
Moon Dev's Code Validator for RBI Pipeline

Validates code between each phase to catch errors early:
  Phase 1->2: Syntax check, import verification
  Phase 2->3: backtesting.py structure, talib usage, column names
  Phase 3->4: No backtesting.lib references, all indicators use talib
  Phase 4->5: Full execution test with timeout

This replaces the fragile GUI-based debug loop with programmatic validation.
"""

import ast
import re
import sys
import subprocess
import tempfile
from dataclasses import dataclass, field
from typing import List, Optional, Tuple
from pathlib import Path
from termcolor import cprint


@dataclass
class ValidationResult:
    """Result of code validation"""
    passed: bool
    errors: List[str] = field(default_factory=list)
    warnings: List[str] = field(default_factory=list)
    suggestions: List[str] = field(default_factory=list)

    def add_error(self, msg: str):
        self.errors.append(msg)
        self.passed = False

    def add_warning(self, msg: str):
        self.warnings.append(msg)

    def add_suggestion(self, msg: str):
        self.suggestions.append(msg)

    def summary(self) -> str:
        lines = []
        if self.passed:
            lines.append("PASSED")
        else:
            lines.append("FAILED")
        for e in self.errors:
            lines.append(f"  ERROR: {e}")
        for w in self.warnings:
            lines.append(f"  WARN: {w}")
        for s in self.suggestions:
            lines.append(f"  TIP: {s}")
        return "\n".join(lines)


class CodeValidator:
    """Validates Python code for the RBI backtesting pipeline"""

    # Required columns for backtesting.py
    REQUIRED_COLUMNS = {"Open", "High", "Low", "Close", "Volume"}

    # Required imports for backtesting
    REQUIRED_IMPORTS = {"pandas", "backtesting"}

    # Forbidden patterns (backtesting.lib should not appear after packaging)
    FORBIDDEN_PATTERNS = [
        (r"from\s+backtesting\.lib\s+import", "backtesting.lib import found - use talib instead"),
        (r"backtesting\.lib\.", "backtesting.lib reference found - use talib instead"),
    ]

    # Required patterns for backtesting code
    REQUIRED_PATTERNS = [
        (r"from\s+backtesting\s+import", "Missing backtesting import"),
        (r"class\s+\w+\(Strategy\)", "Missing Strategy class definition"),
        (r"def\s+init\s*\(\s*self\s*\)", "Missing init() method"),
        (r"def\s+next\s*\(\s*self\s*\)", "Missing next() method"),
        (r"bt\.run\(\)", "Missing bt.run() call"),
        (r"print\s*\(\s*stats", "Missing stats print"),
    ]

    # Data loading patterns
    DATA_LOADING_PATTERNS = [
        (r"pd\.read_csv", "Missing data loading with pd.read_csv"),
        (r"dropna", "Missing dropna for clean data"),
    ]

    def validate_syntax(self, code: str) -> ValidationResult:
        """Validate Python syntax"""
        result = ValidationResult(passed=True)

        try:
            ast.parse(code)
        except SyntaxError as e:
            result.add_error(f"Syntax error at line {e.lineno}: {e.msg}")
            if e.text:
                result.add_suggestion(f"Problematic line: {e.text.strip()}")

        return result

    def validate_imports(self, code: str) -> ValidationResult:
        """Validate that required imports are present"""
        result = ValidationResult(passed=True)

        # Check for required imports
        if "import pandas" not in code and "from pandas" not in code:
            result.add_error("Missing 'import pandas' - required for data handling")

        if "import talib" not in code and "from talib" not in code:
            result.add_warning("No talib import found - indicators may use backtesting.lib")

        if "import numpy" not in code and "from numpy" not in code:
            result.add_warning("No numpy import - may be needed for calculations")

        # Check for backtesting imports
        if "from backtesting" not in code and "import backtesting" not in code:
            result.add_error("Missing backtesting import")

        return result

    def validate_backtest_structure(self, code: str) -> ValidationResult:
        """Validate backtesting.py code structure"""
        result = ValidationResult(passed=True)

        for pattern, msg in self.REQUIRED_PATTERNS:
            if not re.search(pattern, code):
                result.add_error(msg)

        return result

    def validate_data_loading(self, code: str) -> ValidationResult:
        """Validate data loading follows correct pattern"""
        result = ValidationResult(passed=True)

        for pattern, msg in self.DATA_LOADING_PATTERNS:
            if not re.search(pattern, code):
                result.add_warning(msg)

        # Check column mapping
        if "rename" not in code and "mapping" not in code:
            result.add_warning("No column rename/mapping found - backtesting.py requires Open/High/Low/Close/Volume")

        # Check for datetime index
        if "set_index" not in code:
            result.add_warning("No set_index() found - data should have datetime index")

        return result

    def validate_talib_usage(self, code: str) -> ValidationResult:
        """Validate that talib is used correctly instead of backtesting.lib"""
        result = ValidationResult(passed=True)

        for pattern, msg in self.FORBIDDEN_PATTERNS:
            if re.search(pattern, code):
                result.add_error(msg)

        # Check for self.I() usage with talib
        if "self.I(" in code:
            result.add_suggestion("Good: using self.I() for indicators")
        elif "talib." in code:
            result.add_warning("talib used without self.I() - indicators may not work in backtesting.py")

        return result

    def validate_position_sizing(self, code: str) -> ValidationResult:
        """Validate position sizing is safe"""
        result = ValidationResult(passed=True)

        # Check for int() rounding
        if "int(round(" in code:
            result.add_suggestion("Good: position size is properly rounded with int(round())")
        elif "size" in code.lower():
            result.add_warning("Position size may not be properly rounded - use int(round(size))")

        return result

    def validate_no_bugs(self, code: str) -> ValidationResult:
        """Check for common bugs in backtesting code"""
        result = ValidationResult(passed=True)

        # Check for common mistakes
        if "self.data.Close[-1]" in code and "self.I(" not in code:
            result.add_warning("Using self.data.Close directly - may need self.I() wrapper")

        # Check for division by zero
        if re.search(r"/\s*0\b", code):
            result.add_error("Possible division by zero detected")

        # Check for infinite loops
        if "while True" in code:
            result.add_warning("while True loop detected - ensure there's a break condition")

        return result

    def validate_all(self, code: str, phase: str = "backtest") -> ValidationResult:
        """Run all validations for a given phase"""
        result = ValidationResult(passed=True)

        cprint(f"\n[VALIDATOR] Running {phase} phase validations...", "cyan")

        # Always check syntax
        syntax = self.validate_syntax(code)
        if not syntax.passed:
            result.errors.extend(syntax.errors)
            result.passed = False
        result.warnings.extend(syntax.warnings)
        result.suggestions.extend(syntax.suggestions)

        # Phase-specific validations
        if phase in ("backtest", "package", "debug", "final"):
            imports = self.validate_imports(code)
            if not imports.passed:
                result.errors.extend(imports.errors)
                result.passed = False
            result.warnings.extend(imports.warnings)

        if phase in ("backtest", "package", "debug", "final"):
            structure = self.validate_backtest_structure(code)
            if not structure.passed:
                result.errors.extend(structure.errors)
                result.passed = False

        if phase in ("backtest", "package", "debug", "final"):
            data = self.validate_data_loading(code)
            result.warnings.extend(data.warnings)

        if phase in ("package", "debug", "final"):
            talib = self.validate_talib_usage(code)
            if not talib.passed:
                result.errors.extend(talib.errors)
                result.passed = False
            result.warnings.extend(talib.warnings)
            result.suggestions.extend(talib.suggestions)

        if phase in ("backtest", "package", "debug", "final"):
            sizing = self.validate_position_sizing(code)
            result.warnings.extend(sizing.warnings)
            result.suggestions.extend(sizing.suggestions)

        bugs = self.validate_no_bugs(code)
        result.warnings.extend(bugs.warnings)
        if bugs.errors:
            result.errors.extend(bugs.errors)
            result.passed = False

        # Log results
        if result.passed:
            cprint(f"[VALIDATOR] {phase} phase: PASSED", "green")
        else:
            cprint(f"[VALIDATOR] {phase} phase: FAILED ({len(result.errors)} errors)", "red")
        for w in result.warnings:
            cprint(f"[VALIDATOR]   WARN: {w}", "yellow")

        return result

    def dry_run(self, code: str, timeout: int = 30) -> ValidationResult:
        """Try to compile and partially execute the code"""
        result = ValidationResult(passed=True)

        # Step 1: Try to compile
        try:
            compile(code, "<backtest>", "exec")
        except SyntaxError as e:
            result.add_error(f"Compilation failed: {e}")
            return result

        # Step 2: Try to import dependencies
        try:
            test_code = """
import sys
import importlib
missing = []
for mod in ['pandas', 'numpy', 'backtesting', 'talib']:
    try:
        importlib.import_module(mod)
    except ImportError:
        missing.append(mod)
if missing:
    print(f"MISSING_DEPS:{','.join(missing)}")
else:
    print("DEPS_OK")
"""
            res = subprocess.run(
                [sys.executable, "-c", test_code],
                capture_output=True, text=True, timeout=timeout,
            )
            if "MISSING_DEPS" in res.stdout:
                deps = res.stdout.split(":")[1].strip()
                result.add_error(f"Missing dependencies: {deps}")
        except Exception as e:
            result.add_warning(f"Could not check dependencies: {e}")

        return result

    def generate_fix_context(self, code: str, errors: List[str]) -> str:
        """Generate context for the debug agent to fix issues"""
        context = f"""The following backtest code has errors that need fixing:

ERRORS:
{chr(10).join(f'- {e}' for e in errors)}

CURRENT CODE:
```python
{code}
```

Fix ONLY the errors listed above. Keep the rest of the code unchanged.
Return ONLY the fixed Python code block.
"""
        return context
