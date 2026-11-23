"""Tests for --fixing-belief flag on code review."""

from reasonsforge.forge.code.review.prompts.review import build_review_prompt


SAMPLE_BELIEFS = """# Beliefs

### `cli-exceptions-uncaught` [IN]
The CLI does not catch operational exceptions.

### `cli-always-returns-zero` [IN]
The CLI always returns exit code 0 regardless of outcome.

### `cli-has-help` [IN]
The CLI supports --help flag.
"""

SAMPLE_DIFF = """\
diff --git a/cli.py b/cli.py
--- a/cli.py
+++ b/cli.py
@@ -1,5 +1,10 @@
+import sys
+
 def main():
-    run()
+    try:
+        run()
+    except Exception as e:
+        print(f"Error: {e}", file=sys.stderr)
+        return 1
"""


class TestFixingBeliefPrompt:
    def test_no_fixing_beliefs(self):
        prompt = build_review_prompt(SAMPLE_DIFF, beliefs_content=SAMPLE_BELIEFS)
        beliefs_section = prompt.split("## Known Beliefs")[1].split("## Code Changes")[
            0
        ]
        assert "Beliefs Being Fixed" not in beliefs_section

    def test_fixing_belief_adds_instruction(self):
        prompt = build_review_prompt(
            SAMPLE_DIFF,
            beliefs_content=SAMPLE_BELIEFS,
            fixing_beliefs=["cli-exceptions-uncaught"],
        )
        beliefs_section = prompt.split("## Known Beliefs")[1].split("## Code Changes")[
            0
        ]
        assert "`cli-exceptions-uncaught`" in beliefs_section
        assert "success condition" in beliefs_section.lower()

    def test_fixing_belief_does_not_remove_beliefs(self):
        prompt = build_review_prompt(
            SAMPLE_DIFF,
            beliefs_content=SAMPLE_BELIEFS,
            fixing_beliefs=["cli-exceptions-uncaught"],
        )
        assert "cli-exceptions-uncaught" in prompt
        assert "cli-always-returns-zero" in prompt
        assert "cli-has-help" in prompt

    def test_multiple_fixing_beliefs(self):
        prompt = build_review_prompt(
            SAMPLE_DIFF,
            beliefs_content=SAMPLE_BELIEFS,
            fixing_beliefs=["cli-exceptions-uncaught", "cli-always-returns-zero"],
        )
        assert "`cli-exceptions-uncaught`" in prompt
        assert "`cli-always-returns-zero`" in prompt

    def test_fixes_verdict_in_template(self):
        prompt = build_review_prompt(
            SAMPLE_DIFF,
            beliefs_content=SAMPLE_BELIEFS,
            fixing_beliefs=["cli-exceptions-uncaught"],
        )
        assert "FIXES" in prompt
        assert "BELIEF_COMPLIANCE: CONSISTENT | VIOLATES | FIXES | N/A" in prompt

    def test_empty_fixing_beliefs_list(self):
        prompt = build_review_prompt(
            SAMPLE_DIFF,
            beliefs_content=SAMPLE_BELIEFS,
            fixing_beliefs=[],
        )
        beliefs_section = prompt.split("## Known Beliefs")[1].split("## Code Changes")[
            0
        ]
        assert "Beliefs Being Fixed" not in beliefs_section

    def test_no_beliefs_content_ignores_fixing(self):
        prompt = build_review_prompt(
            SAMPLE_DIFF,
            fixing_beliefs=["cli-exceptions-uncaught"],
        )
        assert "Known Beliefs" not in prompt

    def test_violates_instruction_preserved_for_other_beliefs(self):
        prompt = build_review_prompt(
            SAMPLE_DIFF,
            beliefs_content=SAMPLE_BELIEFS,
            fixing_beliefs=["cli-exceptions-uncaught"],
        )
        assert "NOT in the fixing list" in prompt
