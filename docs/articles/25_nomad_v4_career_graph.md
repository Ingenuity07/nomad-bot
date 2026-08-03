# Nomad V4: Career Knowledge Graph, Multi-Renderer & Git-Style Spec Diff

This document details the architectural specifications and design patterns introduced in **Nomad V4**.

---

## 1. Professional Career Knowledge Graph

To eliminate model hallucinations and anchor generation strictly in evidence, Nomad V4 migrates from flat database tables to a structured career graph model:

```
    [Skill]  ◄────►  [SkillExperienceLink]  ◄────►  [Experience]
       ▲                                                 ▲
       │                                                 │
[SkillProjectLink]                                [ProjectExperienceLink]
       │                                                 │
       ▼                                                 ▼
   [Project] ◄───────────────────────────────────────────┘
```

*   **`SkillExperienceLink`**: Maps which skills were applied in which job role.
*   **`SkillProjectLink`**: Links skills directly to evidentiary projects.
*   **`ProjectExperienceLink`**: Binds key projects to their corresponding professional environments.

---

## 2. Multi-Renderer Compilers (`core/resume/renderers/`)

Nomad V4 separates the intermediate canonical representation (`StructuredResumeSpec` JSON) from output styling, providing four independent renderers:

1.  **LaTeX Compiler (`latex.py`)**: Renders highly formatted LaTeX source code and compiles it into a `.pdf` file.
2.  **HTML Compiler (`html.py`)**: Yields an interactive, responsive CSS grid-styled page for portfolio websites.
3.  **DOCX Compiler (`docx.py`)**: Generates structured Word documents matching traditional formatting styles.
4.  **Markdown Compiler (`markdown.py`)**: Outputs clean GitHub-friendly markdown.

---

## 3. Specification Diff Engine (`core/resume/diff.py`)

To let users track changes between iterations, the `SpecDiffEngine` computes deltas on structured specs using python's `difflib.ndiff`:

*   **`summary_diff`**: Line-by-line inline summary differences.
*   **`experience_diffs`**: Company and role matching, showing added/removed bullet points.
*   **`project_diffs`**: Project title matching and corresponding bullet modifications.
