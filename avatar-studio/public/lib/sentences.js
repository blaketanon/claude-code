/**
 * Buffers streamed text and calls emit() with each complete sentence. Very short sentences
 * ("Yeah." "Oh!") are merged into the next one so each speech request carries enough text.
 */
export function sentenceSplitter(emit, minChars = 24) {
  let buf = "";
  const boundary = /[.!?…]+["')\]]*\s+|\n+/g;
  return {
    push(text) {
      buf += text;
      let cut = 0;
      for (const m of buf.matchAll(boundary)) {
        const end = m.index + m[0].length;
        if (buf.slice(cut, end).trim().length >= minChars) {
          emit(buf.slice(cut, end).trim());
          cut = end;
        }
      }
      buf = buf.slice(cut);
    },
    flush() {
      if (buf.trim()) emit(buf.trim());
      buf = "";
    },
  };
}
