"""Sample patch fixtures for unit tests.

These are real unified diffs used to test the executor, reflector, and
patch_generator without making live API calls.
"""

# A valid patch that fixes the add() bug in sample_repo/main.py
VALID_PATCH = """\
--- a/main.py
+++ b/main.py
@@ -8,4 +8,4 @@
 def add(a: int, b: int) -> int:
     \"\"\"Return the sum of two integers.\"\"\"
-    return a - b  # BUG: should be a + b
+    return a + b
"""

# A patch that is syntactically valid but fixes the wrong thing
WRONG_FIX_PATCH = """\
--- a/main.py
+++ b/main.py
@@ -8,4 +8,4 @@
 def add(a: int, b: int) -> int:
     \"\"\"Return the sum of two integers.\"\"\"
-    return a - b  # BUG: should be a + b
+    return a * b  # still wrong
"""

# A patch that cannot be applied (corrupt diff headers)
INVALID_PATCH = """\
This is not a valid unified diff.
It has no headers or hunks.
"""

# A CANNOT_FIX response from the LLM
CANNOT_FIX_RESPONSE = "CANNOT_FIX — the bug is in an external C extension that cannot be patched via Python source edits."
