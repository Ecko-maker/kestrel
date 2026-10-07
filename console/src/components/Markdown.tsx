import { createContext, useContext, type ComponentProps } from "react";
import ReactMarkdown, { type Components } from "react-markdown";
import remarkGfm from "remark-gfm";
import { imageLoads } from "../imagePolicy";

// Hosts answer images may load from (KESTREL_IMAGE_ALLOWLIST, sent in /api/session). Empty: only
// the console's own images load.
export const ImageAllowlist = createContext<readonly string[]>([]);

// Defined once, outside render: links in answers open in a new tab, without access to this page.
function ExternalLink({ href, children }: ComponentProps<"a">) {
  return (
    <a href={href} target="_blank" rel="noopener noreferrer">
      {children}
    </a>
  );
}

// Tiers v2, defense d: an image from anywhere else becomes a plain link showing its full URL, so
// nothing is fetched until the user clicks it.
function AnswerImage({ src, alt }: ComponentProps<"img">) {
  const allowlist = useContext(ImageAllowlist);
  const url = typeof src === "string" ? src : "";
  if (imageLoads(url, allowlist, window.location.origin)) return <img src={url} alt={alt ?? ""} />;
  return (
    <a
      href={url || undefined}
      target="_blank"
      rel="noopener noreferrer"
      className="break-all"
      title="Image not loaded: only images from this console or allowlisted hosts load by themselves"
    >
      [image{alt ? `: ${alt}` : ""}] {url}
    </a>
  );
}

const COMPONENTS: Components = { a: ExternalLink, img: AnswerImage };
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
