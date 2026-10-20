"""Document generation: a model (Gemini Flash) writes human-readable Markdown, our code renders DOCX/PDF/Markdown.

The model never touches files, fonts or paths. It produces text; `parse_markdown` turns a restricted Markdown
subset into a typed `Document`; renderers write it into a fixed output directory under a code-chosen name.
"""
from .model import Block, DocStyle, Document, DocumentError  # noqa: F401
