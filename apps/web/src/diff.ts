// Reading a unified diff, for the one thing the editor does with it: marking
// the lines that changed.
//
// Only additions are marked. A removed line has no line number in the file you
// are looking at, and a "changed" line appears here as an addition — so marking
// additions is exactly the set of lines whose content is new.

export function addedLines(diffText: string, path: string): number[] {
  const wanted = path.replace(/^\/workspace\//, "");
  const lines: number[] = [];
  let current: string | null = null;
  let newLine = 0;

  for (const raw of diffText.split("\n")) {
    if (raw.startsWith("diff --git ")) {
      const match = /^diff --git a\/(.+?) b\/(.+)$/.exec(raw);
      current = match ? match[2] : null;
      continue;
    }
    if (raw.startsWith("+++ ")) {
      const file = raw.slice(4).trim();
      current = file === "/dev/null" ? null : file.replace(/^b\//, "");
      continue;
    }
    if (raw.startsWith("@@")) {
      const match = /^@@ -\d+(?:,\d+)? \+(\d+)(?:,\d+)? @@/.exec(raw);
      newLine = match ? Number(match[1]) : 0;
      continue;
    }
    if (current !== wanted || newLine === 0) continue;

    if (raw.startsWith("+") && !raw.startsWith("+++")) {
      lines.push(newLine);
      newLine += 1;
    } else if (raw.startsWith("-") && !raw.startsWith("---")) {
      // A deletion shifts nothing in the new file.
    } else {
      newLine += 1;
    }
  }
  return lines;
}
