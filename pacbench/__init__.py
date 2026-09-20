"""PACBench — a benchmark for memory systems used by persistent personal assistants.

Measures how well a memory system supports a personal assistant through a realistic
week of life. Deliberately free of any provider-specific import: the corpus, the
grader, and (later) the runner must never reach into one product's store — a green
grade has to come from a memory system's own answer, not from privileged knowledge
of how any provider happens to lay its rows out. A test enforces the boundary
(`tests/test_pacbench.py`).
"""

VERSION = "0.1.0"
CORPUS_VERSION = "0.1.0"
