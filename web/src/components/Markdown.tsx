import { useMemo } from 'react'
import { marked } from 'marked'
import DOMPurify from 'dompurify'

/** Model output is Markdown; render it, and sanitise because it is untrusted text. */
export function Markdown({ text }: { text: string }) {
  const html = useMemo(() => DOMPurify.sanitize(marked.parse(text || '', { async: false, breaks: true }) as string), [text])
  return <div className="md" dangerouslySetInnerHTML={{ __html: html }} />
}
