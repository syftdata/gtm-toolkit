"""
Robust JSON extraction from mixed text responses.
Handles Gemini responses with markdown, preambles, postambles, etc.
"""

import json
import re
from typing import Optional


def extract_and_parse_json(text: str) -> Optional[dict]:
    """
    Extract valid JSON from mixed text responses using multiple strategies.

    Handles patterns:
    - ```json\n{...}\n```
    - ```{...}```
    - `{...}`
    - Plain JSON with preamble: "Here is the result:\n{...}"
    - Plain JSON with postamble: "{...}\n\nAdditional explanation"
    - Nested JSON objects

    Returns:
        Parsed dict if valid JSON found, None otherwise
    """
    if not text or not text.strip():
        return None

    text = text.strip()

    # Strategy 1: Direct parse (already valid JSON)
    result = _try_direct_parse(text)
    if result:
        return result

    # Strategy 2: Remove triple backtick markdown blocks
    result = _try_markdown_extraction(text)
    if result:
        return result

    # Strategy 3: Remove single backticks
    result = _try_single_backtick_extraction(text)
    if result:
        return result

    # Strategy 4: Extract JSON between first { and last }
    result = _try_brace_extraction(text)
    if result:
        return result

    # Strategy 5: Regex search for JSON-like pattern
    result = _try_regex_extraction(text)
    if result:
        return result

    return None


def _try_direct_parse(text: str) -> Optional[dict]:
    """Try parsing text directly as JSON."""
    try:
        result = json.loads(text)
        if isinstance(result, dict):
            return result
    except json.JSONDecodeError:
        pass
    return None


def _try_markdown_extraction(text: str) -> Optional[dict]:
    """Extract JSON from markdown code blocks."""
    # Pattern: ```json\n...\n``` or ```\n...\n```
    patterns = [
        r'```json\s*\n?(.*?)\n?```',  # ```json ... ```
        r'```\s*\n?(.*?)\n?```',       # ``` ... ```
    ]

    for pattern in patterns:
        match = re.search(pattern, text, re.DOTALL | re.IGNORECASE)
        if match:
            json_text = match.group(1).strip()
            result = _try_direct_parse(json_text)
            if result:
                return result

    return None


def _try_single_backtick_extraction(text: str) -> Optional[dict]:
    """Extract JSON from single backticks."""
    # Pattern: `{...}`
    match = re.search(r'`(\{.*?\})`', text, re.DOTALL)
    if match:
        json_text = match.group(1).strip()
        result = _try_direct_parse(json_text)
        if result:
            return result
    return None


def _try_brace_extraction(text: str) -> Optional[dict]:
    """Extract text between first { and last }."""
    first_brace = text.find('{')
    last_brace = text.rfind('}')

    if first_brace != -1 and last_brace != -1 and first_brace < last_brace:
        json_text = text[first_brace:last_brace + 1]
        result = _try_direct_parse(json_text)
        if result:
            return result

    return None


def _try_regex_extraction(text: str) -> Optional[dict]:
    """Use regex to find JSON object pattern."""
    # Find all potential JSON objects
    # This pattern matches balanced braces (simple version)
    pattern = r'\{[^{}]*(?:\{[^{}]*\}[^{}]*)*\}'

    matches = re.findall(pattern, text, re.DOTALL)

    for match in matches:
        result = _try_direct_parse(match)
        if result:
            return result

    return None
