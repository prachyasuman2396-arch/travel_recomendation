/** Small shadcn-style primitives that need no extra dependencies. */
import type { ComponentProps } from "react";
import { cn } from "@/lib/utils";

export function Card({ className, ...props }: ComponentProps<"div">) {
  return <div className={cn("rounded-[var(--radius-card)] bg-white ring-1 ring-line", className)} {...props} />;
}

export function Badge({ className, tone = "neutral", ...props }: ComponentProps<"span"> & {
  tone?: "neutral" | "good" | "warn" | "bad" | "info";
}) {
  const tones = {
    neutral: "bg-mist text-ink",
    good: "bg-[#dcebe2] text-sal-deep",
    warn: "bg-[#f6e7c4] text-[#6b4a0e]",
    bad: "bg-[#f3d9d4] text-danger",
    info: "bg-[#dbe6f1] text-monsoon",
  };
  return (
    <span
      className={cn("inline-flex items-center gap-1 rounded-full px-2.5 py-0.5 text-xs font-medium", tones[tone],
        className)}
      {...props}
    />
  );
}

const field =
  "w-full rounded-[var(--radius-control)] bg-white px-3.5 text-[15px] ring-1 ring-line placeholder:text-ink-soft/70 " +
  "focus-visible:outline-none focus-visible:ring-2 focus-visible:ring-monsoon aria-[invalid=true]:ring-danger";

export function Input({ className, ...props }: ComponentProps<"input">) {
  return <input className={cn(field, "h-11", className)} {...props} />;
}

export function Textarea({ className, ...props }: ComponentProps<"textarea">) {
  return <textarea className={cn(field, "min-h-24 py-3 leading-relaxed", className)} {...props} />;
}

export function Select({ className, ...props }: ComponentProps<"select">) {
  return <select className={cn(field, "h-11 appearance-none pr-8", className)} {...props} />;
}

export function Label({ className, ...props }: ComponentProps<"label">) {
  return <label className={cn("text-sm font-semibold text-ink", className)} {...props} />;
}

export function FieldError({ message }: { message?: string }) {
  if (!message) return null;
  return <p role="alert" className="mt-1.5 text-sm text-danger">{message}</p>;
}

export function Skeleton({ className, ...props }: ComponentProps<"div">) {
  return <div aria-hidden className={cn("animate-pulse rounded-md bg-mist", className)} {...props} />;
}
