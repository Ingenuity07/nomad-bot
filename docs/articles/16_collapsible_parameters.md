# Collapsible Parameters Accordion & Flex Layout Fixes

We have refactored the frontend's layout model to separate the parameter configuration forms from the core scrollable chat history and resolved flexbox viewport stretching bugs using strict height limits.

---

## 1. The Scroll Compression Bug

### Root Cause
In CSS Flexbox layouts, children (such as `.chat-pane` and `.workspace-pane`) default to `min-height: auto`. This prevents flex items from shrinking smaller than their contents. 
When the chat history or logs grew large, the browser stretched the parent `.workspace-grid` container beyond `90vh`, pushing the input panel and dynamic form controls off-screen and locking the scroll mechanics.

### Solution
We enforced strict bounds and allowed containers to shrink using `min-height` resets:
```css
.sidebar, .chat-pane, .workspace-pane {
  min-height: 0;
  overflow: hidden;
}

.chat-history, .workspace-stream {
  flex: 1;
  min-height: 0;
  overflow-y: auto;
}
```

---

## 2. Collapsible Accordion Separation

To prevent tall parameter inputs (like resume text fields) from compressing the chat display, we relocated the dynamic parameter forms to the top of the chat panel and wrapped them in a collapsible accordion:

```typescript
const [showParams, setShowParams] = useState(true);
```

### Visual Grid Hierarchy

```
+-------------------------------------------------------------+
| AIOS Agent Hub                                     [Ready]  |
+-------------------------------------------------------------+
| SELECT TARGET AGENT [ Job Search Agent (Research loop)  v ] |
+-------------------------------------------------------------+
| JOB SEARCH PARAMETERS                                   [^] |
| +---------------------------------------------------------+ |
| | Job Title: [e.g. Python Developer]   Location: ...      | |
| +---------------------------------------------------------+ |
+-------------------------------------------------------------+
|                                                             |
|   Chat Messages (Scrollable Area)                           |
|                                                             |
+-------------------------------------------------------------+
| [ Ask AIOS anything...                                ] [>] |
+-------------------------------------------------------------+
```

When collapsed, the form content disappears entirely, leaving the maximum vertical space available for reading the chat history.
