"use client";

import { ChevronLeft, ChevronRight, FileText, X } from "lucide-react";
import { AnimatePresence, motion, useReducedMotion } from "motion/react";
import { useEffect, useMemo, useRef, useState } from "react";
import { createPortal } from "react-dom";

const EASE = [0.22, 1, 0.36, 1] as const;

/** Object URLs for image files, revoked when the files change or the component unmounts. */
function useImageUrls(files: File[]): (string | null)[] {
  const urls = useMemo(
    () => files.map((f) => (f.type.startsWith("image/") ? URL.createObjectURL(f) : null)),
    [files],
  );
  useEffect(() => () => urls.forEach((u) => u && URL.revokeObjectURL(u)), [urls]);
  return urls;
}

function size(bytes: number): string {
  return bytes < 1024 * 1024 ? `${Math.max(1, Math.round(bytes / 1024))} KB` : `${(bytes / 1024 / 1024).toFixed(1)} MB`;
}

/** The attached files as tiles: images as thumbnails that open full screen, PDFs as a card. */
export function Attachments({ files, onRemove }: { files: File[]; onRemove: (i: number) => void }) {
  const urls = useImageUrls(files);
  const [open, setOpen] = useState<number | null>(null);
  const images = files.map((_, i) => i).filter((i) => urls[i]);

  if (files.length === 0) return null;
  return (
    <>
      <ul className="mt-4 grid grid-cols-2 gap-3 sm:grid-cols-3">
        {files.map((f, i) => (
          <li key={`${f.name}-${f.size}-${i}`} className="group relative">
            {urls[i] ? (
              <button
                type="button"
                onClick={() => setOpen(i)}
                className="block w-full overflow-hidden rounded-[1rem] border border-line bg-white/[0.02] text-left transition-colors hover:border-line-strong focus-visible:border-blue"
                aria-label={`View ${f.name} full size`}
              >
                {/* eslint-disable-next-line @next/next/no-img-element -- a local preview (object URL) */}
                <img src={urls[i]!} alt="" className="h-28 w-full object-cover object-top transition-transform duration-500 ease-[var(--ease-calm)] group-hover:scale-[1.03]" />
                <span className="block truncate border-t border-line px-3 py-2 text-xs text-text-2">
                  {f.name} <span className="ml-1 text-text-3">{size(f.size)}</span>
                </span>
              </button>
            ) : (
              <div className="flex h-full min-h-[9.25rem] flex-col justify-between rounded-[1rem] border border-line bg-white/[0.02]">
                <div className="grid flex-1 place-items-center">
                  <FileText className="size-8 text-text-3" strokeWidth={1.5} aria-hidden="true" />
                </div>
                <span className="block truncate border-t border-line px-3 py-2 text-xs text-text-2">
                  {f.name} <span className="ml-1 text-text-3">{size(f.size)}</span>
                </span>
              </div>
            )}
            <button
              type="button"
              onClick={() => onRemove(i)}
              aria-label={`Remove ${f.name}`}
              className="absolute top-2 right-2 grid size-7 place-items-center rounded-full border border-line-strong bg-[rgb(5_5_7/0.8)] text-text-2 opacity-90 transition-colors hover:text-text"
            >
              <X className="size-3.5" aria-hidden="true" />
            </button>
          </li>
        ))}
      </ul>
      <Lightbox
        items={images.map((i) => ({ url: urls[i]!, name: files[i].name }))}
        index={open === null ? null : images.indexOf(open)}
        onIndex={(k) => setOpen(k === null ? null : images[k])}
      />
    </>
  );
}

/** Full-screen image viewer: Esc or a click outside closes it; arrow keys move between images. */
export function Lightbox({
  items,
  index,
  onIndex,
}: {
  items: { url: string; name: string }[];
  index: number | null;
  onIndex: (i: number | null) => void;
}) {
  const still = useReducedMotion() === true;
  const closeRef = useRef<HTMLButtonElement>(null);
  const isOpen = index !== null && index >= 0 && index < items.length;

  useEffect(() => {
    if (!isOpen) return;
    const prevOverflow = document.body.style.overflow;
    document.body.style.overflow = "hidden";
    closeRef.current?.focus();
    const onKey = (e: KeyboardEvent) => {
      if (e.key === "Escape") onIndex(null);
      else if (e.key === "ArrowRight" && items.length > 1) onIndex(((index ?? 0) + 1) % items.length);
      else if (e.key === "ArrowLeft" && items.length > 1) onIndex(((index ?? 0) - 1 + items.length) % items.length);
    };
    window.addEventListener("keydown", onKey);
    return () => {
      document.body.style.overflow = prevOverflow;
      window.removeEventListener("keydown", onKey);
    };
  }, [isOpen, index, items.length, onIndex]);

  if (typeof document === "undefined") return null;
  // Rendered at the end of <body>, so no faded or clipped ancestor can affect it.
  return createPortal(
    <AnimatePresence>
      {isOpen ? (
        <motion.div
          key="lightbox"
          role="dialog"
          aria-modal="true"
          aria-label={items[index].name}
          className="fixed inset-0 z-50 flex flex-col items-center justify-center bg-[rgb(5_5_7/0.92)] p-4 sm:p-10"
          onClick={() => onIndex(null)}
          initial={still ? false : { opacity: 0 }}
          animate={{ opacity: 1 }}
          exit={still ? undefined : { opacity: 0 }}
          transition={{ duration: 0.3, ease: EASE }}
        >
          <motion.img
            key={items[index].url}
            src={items[index].url}
            alt={items[index].name}
            onClick={(e) => e.stopPropagation()}
            className="max-h-[82vh] max-w-full rounded-[1.25rem] border border-line-strong object-contain shadow-[0_40px_120px_-30px_rgb(0_0_0/0.9),0_0_0_1px_rgb(110_165_255/0.08)]"
            initial={still ? false : { opacity: 0, scale: 0.96 }}
            animate={{ opacity: 1, scale: 1 }}
            transition={{ duration: 0.45, ease: EASE }}
          />
          <div className="mt-4 flex items-center gap-4 text-sm text-text-2" onClick={(e) => e.stopPropagation()}>
            {items.length > 1 ? (
              <button
                type="button"
                aria-label="Previous image"
                onClick={() => onIndex(((index ?? 0) - 1 + items.length) % items.length)}
                className="grid size-9 place-items-center rounded-full border border-line-strong hover:text-text"
              >
                <ChevronLeft className="size-4" aria-hidden="true" />
              </button>
            ) : null}
            <span className="max-w-[60vw] truncate">
              {items[index].name}
              {items.length > 1 ? <span className="text-text-3"> ({index + 1} of {items.length})</span> : null}
            </span>
            {items.length > 1 ? (
              <button
                type="button"
                aria-label="Next image"
                onClick={() => onIndex(((index ?? 0) + 1) % items.length)}
                className="grid size-9 place-items-center rounded-full border border-line-strong hover:text-text"
              >
                <ChevronRight className="size-4" aria-hidden="true" />
              </button>
            ) : null}
          </div>
          <button
            ref={closeRef}
            type="button"
            aria-label="Close"
            onClick={() => onIndex(null)}
            className="absolute top-4 right-4 grid size-10 place-items-center rounded-full border border-line-strong bg-[rgb(5_5_7/0.6)] text-text-2 hover:text-text sm:top-6 sm:right-6"
          >
            <X className="size-4" aria-hidden="true" />
          </button>
        </motion.div>
      ) : null}
    </AnimatePresence>,
    document.body,
  );
}
