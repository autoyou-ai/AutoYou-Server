/**
 * Safe XSS-proof Markdown renderer & interactive formatting helpers for AutoYou Diary.
 */

export function escapeHtml(str) {
  return String(str || "")
    .replace(/&/g, "&amp;")
    .replace(/</g, "&lt;")
    .replace(/>/g, "&gt;")
    .replace(/"/g, "&quot;")
    .replace(/'/g, "&#39;");
}

function safeHref(raw, basePrefix) {
  const url = String(raw || "").trim();
  if (!url) return "";
  if (url.startsWith("/agent/diary_agent/api/diary/entries/")) {
    return basePrefix + url.slice("/agent/diary_agent".length);
  }
  if (url.startsWith("/api/diary/entries/")) {
    return basePrefix + url;
  }
  if (/^(https?:|mailto:)/i.test(url)) {
    return url;
  }
  return "";
}

function formatInline(text, basePrefix) {
  let out = escapeHtml(text);

  // Inline code spans first (protect contents)
  const codeSlots = [];
  out = out.replace(/`([^`\n]+)`/g, (_, code) => {
    const idx = codeSlots.length;
    codeSlots.push(`<code>${code}</code>`);
    return `\u0000CODE${idx}\u0000`;
  });

  // Images: ![alt](url)
  out = out.replace(/!\[([^\]\n]{0,160})\]\(([^)\s]+)\)/g, (_, alt, url) => {
    const safe = safeHref(url, basePrefix);
    if (!safe) return escapeHtml(alt);
    return `<img class="md-inline-img" src="${escapeHtml(safe)}" alt="${alt}" loading="lazy">`;
  });

  // Links: [label](url)
  out = out.replace(/\[([^\]\n]{1,160})\]\(([^)\s]+)\)/g, (_, label, url) => {
    const safe = safeHref(url, basePrefix);
    if (!safe) return label;
    return `<a href="${escapeHtml(safe)}" target="_blank" rel="noopener noreferrer">${label}</a>`;
  });

  // Bold + italic, bold, italic, strikethrough, highlight
  out = out.replace(/\*\*\*([^*]+)\*\*\*/g, "<strong><em>$1</em></strong>");
  out = out.replace(/\*\*([^*]+)\*\*/g, "<strong>$1</strong>");
  out = out.replace(/__([^_]+)__/g, "<strong>$1</strong>");
  out = out.replace(/(?<!\w)\*([^*\n]+)\*(?!\w)/g, "<em>$1</em>");
  out = out.replace(/(?<!\w)_([^_\n]+)_(?!\w)/g, "<em>$1</em>");
  out = out.replace(/~~([^~]+)~~/g, "<del>$1</del>");
  out = out.replace(/==([^=]+)==/g, "<mark>$1</mark>");

  // Inline hashtags (#tag)
  out = out.replace(/(^|\s)#([A-Za-z0-9][A-Za-z0-9_-]{0,40})\b/g, '$1<span class="md-tag">#$2</span>');

  // Restore inline code
  out = out.replace(/\u0000CODE(\d+)\u0000/g, (_, idx) => codeSlots[Number(idx)] || "");
  return out;
}

/**
 * Renders Markdown safely into HTML with interactive checklist line numbers.
 */
export function renderMarkdown(source, basePrefix = "") {
  const lines = String(source || "").replace(/\r\n/g, "\n").split("\n");
  const blocks = [];
  let i = 0;

  while (i < lines.length) {
    const line = lines[i];

    // Fenced code block
    const fence = line.match(/^```([A-Za-z0-9_-]*)\s*$/);
    if (fence) {
      const lang = fence[1] || "";
      const codeLines = [];
      i += 1;
      while (i < lines.length && !lines[i].match(/^```\s*$/)) {
        codeLines.push(lines[i]);
        i += 1;
      }
      i += 1;
      blocks.push(`<pre class="md-code"><code data-lang="${escapeHtml(lang)}">${escapeHtml(codeLines.join("\n"))}</code></pre>`);
      continue;
    }

    // Blank line
    if (!line.trim()) {
      i += 1;
      continue;
    }

    // Horizontal rule
    if (/^\s*([-*_])\1\1+\s*$/.test(line)) {
      blocks.push("<hr>");
      i += 1;
      continue;
    }

    // Headings
    const heading = line.match(/^(#{1,6})\s+(.*)$/);
    if (heading) {
      const level = Math.min(6, heading[1].length);
      blocks.push(`<h${level}>${formatInline(heading[2], basePrefix)}</h${level}>`);
      i += 1;
      continue;
    }

    // Blockquote
    if (/^\s*>\s?/.test(line)) {
      const quoteLines = [];
      while (i < lines.length && /^\s*>\s?/.test(lines[i])) {
        quoteLines.push(lines[i].replace(/^\s*>\s?/, ""));
        i += 1;
      }
      blocks.push(`<blockquote>${renderMarkdown(quoteLines.join("\n"), basePrefix)}</blockquote>`);
      continue;
    }

    // Checklist items
    if (/^\s*[-*+]\s+\[([ xX])\]\s+/.test(line)) {
      const items = [];
      while (i < lines.length) {
        const m = lines[i].match(/^\s*[-*+]\s+\[([ xX])\]\s+(.*)$/);
        if (!m) break;
        const checked = m[1].toLowerCase() === "x";
        items.push(
          `<li class="md-check-item${checked ? " is-done" : ""}">` +
            `<label><input type="checkbox" data-line="${i}"${checked ? " checked" : ""}>` +
            `<span>${formatInline(m[2], basePrefix)}</span></label></li>`
        );
        i += 1;
      }
      blocks.push(`<ul class="md-checklist">${items.join("")}</ul>`);
      continue;
    }

    // Unordered list
    if (/^\s*[-*+]\s+/.test(line)) {
      const items = [];
      while (i < lines.length) {
        const m = lines[i].match(/^\s*[-*+]\s+(.*)$/);
        if (!m || /^\[[ xX]\]\s+/.test(m[1])) break;
        items.push(`<li>${formatInline(m[1], basePrefix)}</li>`);
        i += 1;
      }
      blocks.push(`<ul>${items.join("")}</ul>`);
      continue;
    }

    // Ordered list
    if (/^\s*\d+\.\s+/.test(line)) {
      const items = [];
      while (i < lines.length) {
        const m = lines[i].match(/^\s*\d+\.\s+(.*)$/);
        if (!m) break;
        items.push(`<li>${formatInline(m[1], basePrefix)}</li>`);
        i += 1;
      }
      blocks.push(`<ol>${items.join("")}</ol>`);
      continue;
    }

    // Paragraph
    const para = [];
    while (
      i < lines.length &&
      lines[i].trim() &&
      !/^(#{1,6}\s+|```|\s*>\s?|\s*[-*+]\s+|\s*\d+\.\s+|\s*([-*_])\1\1+\s*$)/.test(lines[i])
    ) {
      para.push(lines[i]);
      i += 1;
    }
    blocks.push(`<p>${para.map((l) => formatInline(l, basePrefix)).join("<br>")}</p>`);
  }

  return blocks.join("\n");
}

/**
 * Toggle a checklist checkbox on a specific 0-based line inside raw Markdown.
 */
export function toggleChecklistLine(source, lineIndex) {
  const lines = String(source || "").replace(/\r\n/g, "\n").split("\n");
  if (lineIndex < 0 || lineIndex >= lines.length) return source;
  lines[lineIndex] = lines[lineIndex].replace(
    /^(\s*[-*+]\s+\[)([ xX])(\]\s+)/,
    (_, pre, mark, post) => pre + (mark.toLowerCase() === "x" ? " " : "x") + post
  );
  return lines.join("\n");
}

/**
 * Apply Markdown formatting command to a textarea selection cleanly.
 */
export function applyMarkdownCommand(textarea, cmd) {
  const start = textarea.selectionStart ?? 0;
  const end = textarea.selectionEnd ?? start;
  const value = textarea.value;
  const selected = value.slice(start, end);

  const replace = (text, selStartOffset, selEndOffset) => {
    textarea.value = value.slice(0, start) + text + value.slice(end);
    textarea.focus();
    textarea.setSelectionRange(start + selStartOffset, start + selEndOffset);
    textarea.dispatchEvent(new Event("input", { bubbles: true }));
  };

  const prefixLines = (prefix) => {
    const lineStart = value.lastIndexOf("\n", start - 1) + 1;
    const block = value.slice(lineStart, end);
    const updated = block
      .split("\n")
      .map((l) => (l.startsWith(prefix) ? l.slice(prefix.length) : prefix + l))
      .join("\n");
    textarea.value = value.slice(0, lineStart) + updated + value.slice(end);
    textarea.focus();
    textarea.setSelectionRange(lineStart, lineStart + updated.length);
    textarea.dispatchEvent(new Event("input", { bubbles: true }));
  };

  switch (cmd) {
    case "heading":
      prefixLines("## ");
      break;
    case "bold": {
      const inner = selected || "bold text";
      replace(`**${inner}**`, 2, 2 + inner.length);
      break;
    }
    case "italic": {
      const inner = selected || "italic text";
      replace(`*${inner}*`, 1, 1 + inner.length);
      break;
    }
    case "checklist":
      prefixLines("- [ ] ");
      break;
    case "bullet":
      prefixLines("- ");
      break;
    case "quote":
      prefixLines("> ");
      break;
    case "code":
      if (selected.includes("\n")) {
        replace("```\n" + selected + "\n```", 4, 4 + selected.length);
      } else {
        const inner = selected || "code";
        replace("`" + inner + "`", 1, 1 + inner.length);
      }
      break;
    case "link": {
      const label = selected || "Link title";
      replace(`[${label}](https://)`, label.length + 3, label.length + 11);
      break;
    }
    case "hr": {
      const insert = (start > 0 && value[start - 1] !== "\n" ? "\n\n" : "\n") + "---\n\n";
      replace(insert, insert.length, insert.length);
      break;
    }
  }
}

export function stripMarkdownPreview(text) {
  return String(text || "")
    .replace(/!\[[^\]]*\]\([^)]*\)/g, "")
    .replace(/\[([^\]]+)\]\([^)]*\)/g, "$1")
    .replace(/^#{1,6}\s+/gm, "")
    .replace(/[*_`~>#]/g, "")
    .replace(/\s+/g, " ")
    .trim();
}
