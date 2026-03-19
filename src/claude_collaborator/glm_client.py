"""
GLM Client for AI Research Tasks
Handles communication with GLM-5 API
"""

import os
from typing import Any, Dict, List, Optional
from dotenv import load_dotenv

# Load environment variables
load_dotenv()


class GLMClient:
    """Client for GLM-5 API"""

    def __init__(self):
        """Initialize GLM client"""
        self.api_key = os.getenv("GLM_API_KEY")
        self.model = os.getenv("GLM_MODEL", "glm-5")
        self.base_url = "https://api.z.ai/api/paas/v4"
        self.timeout = 120  # 120 second timeout for API calls

        if not self.api_key:
            raise ValueError("GLM_API_KEY not found in environment variables")

    def explore(
        self,
        question: str,
        context: str = "",
        max_tokens: int = 2048
    ) -> str:
        """
        Ask GLM to explore a codebase question

        Args:
            question: The exploration question
            context: Additional code context or snippets
            max_tokens: Maximum response tokens

        Returns:
            GLM's analysis
        """
        try:
            from zai import ZaiClient

            client = ZaiClient(api_key=self.api_key)

            # Build prompt
            prompt = f"""You are a codebase research assistant. Analyze the following question about a codebase.

Question: {question}

{f"Context:\n{context}" if context else ""}

Provide a comprehensive analysis including:
1. Overview of what you found
2. Key components or patterns
3. Dependencies or relationships
4. Any recommendations or observations

Be specific and reference code elements when possible."""

            response = client.chat.completions.create(
                model=self.model,
                messages=[
                    {
                        "role": "user",
                        "content": prompt
                    }
                ],
                max_tokens=max_tokens,
                temperature=1.0,
                timeout=self.timeout
            )

            # GLM-5 puts reasoning in reasoning_content field
            message = response.choices[0].message
            content = message.content or message.reasoning_content or ""
            return content

        except ImportError:
            # Fallback to OpenAI-compatible API
            return self._explore_openai_compat(question, context, max_tokens)

    def _explore_openai_compat(
        self,
        question: str,
        context: str,
        max_tokens: int
    ) -> str:
        """Use OpenAI-compatible API for GLM"""
        try:
            from openai import OpenAI

            client = OpenAI(
                api_key=self.api_key,
                base_url=self.base_url
            )

            prompt = f"""You are a codebase research assistant. Analyze the following question about a codebase.

Question: {question}

{f"Context:\n{context}" if context else ""}

Provide a comprehensive analysis including:
1. Overview of what you found
2. Key components or patterns
3. Dependencies or relationships
4. Any recommendations or observations

Be specific and reference code elements when possible."""

            response = client.chat.completions.create(
                model=self.model,
                messages=[
                    {
                        "role": "user",
                        "content": prompt
                    }
                ],
                max_tokens=max_tokens,
                temperature=1.0,
                timeout=self.timeout
            )

            # GLM-5 puts reasoning in reasoning_content field
            message = response.choices[0].message
            content = message.content or message.reasoning_content or ""
            return content

        except Exception as e:
            return f"Error calling GLM API: {str(e)}"

    def compare(
        self,
        code1: str,
        code2: str,
        labels: Optional[List[str]] = None
    ) -> str:
        """
        Compare two code sections

        Args:
            code1: First code section
            code2: Second code section
            labels: Optional labels for the sections

        Returns:
            Comparison analysis
        """
        label1 = labels[0] if labels and len(labels) > 0 else "Code 1"
        label2 = labels[1] if labels and len(labels) > 1 else "Code 2"

        try:
            from zai import ZaiClient

            client = ZaiClient(api_key=self.api_key)

            prompt = f"""Compare these two code sections:

{label1}:
```
{code1}
```

{label2}:
```
{code2}
```

Provide:
1. Similarities
2. Differences
3. Which approach is better and why
4. Any recommendations"""

            response = client.chat.completions.create(
                model=self.model,
                messages=[{"role": "user", "content": prompt}],
                max_tokens=2048,
                temperature=1.0,
                timeout=self.timeout
            )

            # GLM-5 puts reasoning in reasoning_content field
            message = response.choices[0].message
            content = message.content or message.reasoning_content or ""
            return content

        except Exception as e:
            return f"Error comparing code: {str(e)}"

    def deep_dive(
        self,
        topic: str,
        code_files: Dict[str, str],
        focus_areas: Optional[List[str]] = None
    ) -> str:
        """
        Perform deep dive analysis on a topic

        Args:
            topic: Topic to analyze
            code_files: Dictionary of filenames to code content
            focus_areas: Specific areas to focus on

        Returns:
            Comprehensive analysis
        """
        try:
            from zai import ZaiClient

            client = ZaiClient(api_key=self.api_key)

            # Build prompt with code files
            files_section = ""
            for filename, content in code_files.items():
                files_section += f"\nFile: {filename}\n```\n{content}\n```\n"

            focus_section = ""
            if focus_areas:
                focus_section = f"\nFocus on these areas:\n" + "\n".join(f"- {a}" for a in focus_areas)

            prompt = f"""Perform a deep dive analysis on: {topic}{focus_section}

Relevant code files:
{files_section}

Provide a comprehensive analysis including:
1. Overall architecture and design
2. Key patterns and conventions
3. Dependencies and relationships
4. Potential issues or improvements
5. How this integrates with the broader codebase"""

            response = client.chat.completions.create(
                model=self.model,
                messages=[{"role": "user", "content": prompt}],
                max_tokens=4096,
                temperature=1.0,
                timeout=self.timeout
            )

            # GLM-5 puts reasoning in reasoning_content field
            message = response.choices[0].message
            content = message.content or message.reasoning_content or ""
            return content

        except Exception as e:
            return f"Error performing deep dive: {str(e)}"
