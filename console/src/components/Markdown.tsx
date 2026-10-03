import type { ComponentProps } from "react";
import ReactMarkdown, { type Components } from "react-markdown";
import remarkGfm from "remark-gfm";

// Defined once, outside render: links in answers open in a new tab, without access to this page.
function ExternalLink({ href, children }: ComponentProps<"a">) {
  return (
    <a href={href} target="_blank" rel="noopener noreferrer">
      {children}
    </a>
  );
}

const COMPONENTS: Components = { a: ExternalLink };
const PLUGINS = [remarkGfm];

export function Markdown({ text, streaming }: { text: string; streaming?: boolean }) {
  return (
    <div className={streaming ? "md cursor-blink" : "md"}>
      <ReactMarkdown remarkPlugins={PLUGINS} components={COMPONENTS}>
        {text}
      </ReactMarkdown>
    </div>
  );
}
