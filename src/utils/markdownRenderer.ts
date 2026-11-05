export interface TocItem {
  id: string;
  level: number;
  text: string;
}

export function escapeHtml(str: string): string {
  return String(str || '')
    .replace(/&/g, '&amp;')
    .replace(/</g, '&lt;')
    .replace(/>/g, '&gt;')
    .replace(/"/g, '&quot;');
}

export function highlightCodeSyntax(rawCode: string, lang: string): string {
  let html = escapeHtml(rawCode);
  const l = (lang || '').toLowerCase();
  const tokens: string[] = [];
  const saveToken = (cls: string, match: string) => {
    const id = `@@TOK_${tokens.length}@@`;
    tokens.push(`<span class="${cls}">${match}</span>`);
    return id;
  };

  if (l === 'sql') {
    html = html.replace(/(--[^\n]*)/g, (m) => saveToken('tok-cmt', m));
    html = html.replace(/('[^']*')/g, (m) => saveToken('tok-str', m));
    html = html.replace(
      /\b(SELECT|FROM|WHERE|UPDATE|SET|DELETE|INSERT|INTO|VALUES|AND|OR|IN|DISTINCT|GROUP|BY|ORDER|LEFT|JOIN|ON|AS|HAVING|SUM|CASE|WHEN|THEN|ELSE|END|LIKE|REPLACE|RTRIM)\b/gi,
      (m) => saveToken('tok-kw', m)
    );
  } else if (l === 'yaml' || l === 'yml') {
    html = html.replace(/(#[^\n]*)/g, (m) => saveToken('tok-cmt', m));
    html = html.replace(/("[^"]*"|'[^']*')/g, (m) => saveToken('tok-str', m));
    html = html.replace(/^(\s*[\w.-]+)(?=:)/gm, (m) => saveToken('tok-key', m));
  } else {
    html = html.replace(/(#[^\n]*)/g, (m) => saveToken('tok-cmt', m));
    html = html.replace(/("[^"\n]*"|'[^'\n]*')/g, (m) => saveToken('tok-str', m));
    html = html.replace(
      /\b(def|class|return|import|from|if|elif|else|for|while|try|except|finally|with|as|True|False|None|and|or|not|in|is|raise|pass|continue|break|async|await|python3|python|curl|git)\b/g,
      (m) => saveToken('tok-kw', m)
    );
  }

  html = html.replace(/\b(\d+(?:\.\d+)?)\b/g, (m) => saveToken('tok-num', m));
  tokens.forEach((spanHtml, idx) => {
    html = html.replace(`@@TOK_${idx}@@`, () => spanHtml);
  });
  return html;
}

export function formatInlineMarkdown(text: string, searchKeyword = ''): string {
  let s = escapeHtml(text);

  const codePlaceholders: string[] = [];
  s = s.replace(/`([^`]+)`/g, (_, codeContent) => {
    const idx = codePlaceholders.length;
    let extraCls = '';
    if (/^L\d+(\s*-\s*L?\d+)?$/i.test(codeContent.trim())) {
      extraCls = ' hl-line';
    } else if (/\.(py|yaml|yml|md|xlsx|csv|sql|html|log|json|env)\b/i.test(codeContent)) {
      extraCls = ' hl-path';
    }
    codePlaceholders.push(`<code class="md-inline-code${extraCls}">${codeContent}</code>`);
    return `@@INLINECODE_${idx}@@`;
  });

  s = s.replace(/\*\*([^*]+)\*\*/g, '<strong>$1</strong>');
  s = s.replace(/\*([^*\n]+)\*/g, '<em>$1</em>');
  s = s.replace(/~~([^~]+)~~/g, '<del>$1</del>');
  s = s.replace(/\[([^\]]+)\]\(([^)]+)\)/g, '<a href="$2" target="_blank" rel="noopener noreferrer">$1</a>');
  s = s.replace(/\b(ALL_OK|已核对 PASS|100% 闭环通过)\b/g, '<span class="status-text-pass">$1</span>');

  codePlaceholders.forEach((cHtml, idx) => {
    s = s.replace(`@@INLINECODE_${idx}@@`, () => cHtml);
  });

  if (searchKeyword && searchKeyword.trim().length > 0) {
    const kwEsc = searchKeyword.trim().replace(/[.*+?^${}()|[\]\\]/g, '\\$&');
    const reg = new RegExp(`(${kwEsc})`, 'gi');
    s = s
      .split(/(<[^>]+>)/g)
      .map((part) => (part.startsWith('<') ? part : part.replace(reg, '<mark class="md-search-hit">$1</mark>')))
      .join('');
  }
  return s;
}

export function renderMarkdownToHtml(mdText: string, searchKeyword = ''): { html: string; toc: TocItem[] } {
  const raw = String(mdText || '').replace(/\r\n/g, '\n');
  const lines = raw.split('\n');
  let idx = 0;
  const htmlOut: string[] = [];
  const tocItems: TocItem[] = [];

  if (lines[0] && lines[0].trim() === '---') {
    let endFm = -1;
    for (let j = 1; j < Math.min(lines.length, 35); j++) {
      if (lines[j].trim() === '---') {
        endFm = j;
        break;
      }
    }
    if (endFm > 1) {
      const fmBody = lines.slice(1, endFm).join('\n');
      htmlOut.push(
        `<div class="md-code-container"><div class="md-code-header"><span>YAML METADATA</span></div><pre class="md-code-pre">${highlightCodeSyntax(fmBody, 'yaml')}</pre></div>`
      );
      idx = endFm + 1;
    }
  }

  let inCodeBlock = false;
  let codeLang = '';
  let codeBuffer: string[] = [];
  let listType: 'ul' | 'ol' | null = null;
  let listBuffer: string[] = [];
  let tableBuffer: string[] = [];

  const flushList = () => {
    if (!listType || !listBuffer.length) return;
    htmlOut.push(`<${listType}>${listBuffer.join('')}</${listType}>`);
    listType = null;
    listBuffer = [];
  };

  const flushTable = () => {
    if (!tableBuffer.length) return;
    if (tableBuffer.length < 2) {
      tableBuffer.forEach((l) => htmlOut.push(`<p>${formatInlineMarkdown(l, searchKeyword)}</p>`));
      tableBuffer = [];
      return;
    }
    const parseRow = (r: string) =>
      r
        .trim()
        .replace(/^\|/, '')
        .replace(/\|$/, '')
        .split('|')
        .map((c) => c.trim());

    const headerCells = parseRow(tableBuffer[0]);
    let bodyStartIdx = 1;
    const aligns: string[] = [];
    if (/^[\s|:-]+$/.test(tableBuffer[1])) {
      const sepCells = parseRow(tableBuffer[1]);
      sepCells.forEach((sc) => {
        if (sc.startsWith(':') && sc.endsWith(':')) aligns.push('center');
        else if (sc.endsWith(':')) aligns.push('right');
        else aligns.push('left');
      });
      bodyStartIdx = 2;
    }

    const thHtml = headerCells
      .map((h, i) => `<th style="text-align:${aligns[i] || 'left'}">${formatInlineMarkdown(h, searchKeyword)}</th>`)
      .join('');
    const rowsHtml = tableBuffer
      .slice(bodyStartIdx)
      .map((rowLine) => {
        const cells = parseRow(rowLine);
        const tds = headerCells
          .map((_, i) => `<td style="text-align:${aligns[i] || 'left'}">${formatInlineMarkdown(cells[i] || '', searchKeyword)}</td>`)
          .join('');
        return `<tr>${tds}</tr>`;
      })
      .join('');

    htmlOut.push(
      `<div class="md-table-wrap"><table class="md-table"><thead><tr>${thHtml}</tr></thead><tbody>${rowsHtml}</tbody></table></div>`
    );
    tableBuffer = [];
  };

  for (; idx < lines.length; idx++) {
    const line = lines[idx];
    const trimmed = line.trim();

    if (trimmed.startsWith('```')) {
      flushList();
      flushTable();
      if (!inCodeBlock) {
        inCodeBlock = true;
        codeLang = trimmed.slice(3).trim() || 'text';
        codeBuffer = [];
      } else {
        const body = codeBuffer.join('\n');
        htmlOut.push(
          `<div class="md-code-container"><div class="md-code-header"><span>${escapeHtml(codeLang.toUpperCase())}</span></div><pre class="md-code-pre">${highlightCodeSyntax(body, codeLang)}</pre></div>`
        );
        inCodeBlock = false;
        codeLang = '';
        codeBuffer = [];
      }
      continue;
    }

    if (inCodeBlock) {
      codeBuffer.push(line);
      continue;
    }

    if (trimmed.startsWith('|') && trimmed.endsWith('|')) {
      flushList();
      tableBuffer.push(trimmed);
      continue;
    } else if (tableBuffer.length > 0) {
      flushTable();
    }

    if (!trimmed) {
      flushList();
      continue;
    }

    if (/^(-{3,}|\*{3,}|_{3,})$/.test(trimmed)) {
      flushList();
      htmlOut.push('<hr />');
      continue;
    }

    const headingMatch = trimmed.match(/^(#{1,4})\s+(.+)$/);
    if (headingMatch) {
      flushList();
      const level = headingMatch[1].length;
      const rawTitle = headingMatch[2].trim();
      const cleanTitle = rawTitle.replace(/[*_`~]/g, '');
      const anchorId = `md-sec-${tocItems.length + 1}`;
      if (level <= 3) {
        tocItems.push({ id: anchorId, level, text: cleanTitle });
      }
      htmlOut.push(`<h${level} id="${anchorId}">${formatInlineMarkdown(rawTitle, searchKeyword)}</h${level}>`);
      continue;
    }

    if (trimmed.startsWith('>')) {
      flushList();
      const quoteBody = trimmed.replace(/^>\s?/, '');
      htmlOut.push(`<blockquote><p>${formatInlineMarkdown(quoteBody, searchKeyword)}</p></blockquote>`);
      continue;
    }

    const ulMatch = trimmed.match(/^[-*+]\s+(.+)$/);
    if (ulMatch) {
      if (listType && listType !== 'ul') flushList();
      listType = 'ul';
      const itemBody = ulMatch[1];
      const taskMatch = itemBody.match(/^\[([ xX])\]\s+(.+)$/);
      if (taskMatch) {
        const checked = taskMatch[1].toLowerCase() === 'x' ? 'checked' : '';
        listBuffer.push(
          `<li class="md-task-item"><input type="checkbox" disabled ${checked} />${formatInlineMarkdown(taskMatch[2], searchKeyword)}</li>`
        );
      } else {
        listBuffer.push(`<li>${formatInlineMarkdown(itemBody, searchKeyword)}</li>`);
      }
      continue;
    }

    const olMatch = trimmed.match(/^\d+\.\s+(.+)$/);
    if (olMatch) {
      if (listType && listType !== 'ol') flushList();
      listType = 'ol';
      listBuffer.push(`<li>${formatInlineMarkdown(olMatch[1], searchKeyword)}</li>`);
      continue;
    }

    flushList();
    htmlOut.push(`<p>${formatInlineMarkdown(trimmed, searchKeyword)}</p>`);
  }

  flushList();
  flushTable();

  return { html: htmlOut.join('\n'), toc: tocItems };
}
