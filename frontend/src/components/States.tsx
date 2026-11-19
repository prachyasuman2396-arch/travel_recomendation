import { AlertTriangle, Compass, RotateCw } from "lucide-react";
import type { ReactNode } from "react";
import { Button } from "@/components/ui/button";
import { Card, Skeleton } from "@/components/ui/primitives";
import { ApiError } from "@/services/api";

export function ErrorState({ error, onRetry, title = "Something went wrong" }: {
  error: unknown;
  onRetry?: () => void;
  title?: string;
}) {
  const message = error instanceof Error ? error.message : "An unexpected error occurred.";
  const requestId = error instanceof ApiError ? error.requestId : null;
  const retryable = !(error instanceof ApiError) || error.retryable;
  return (
    <Card role="alert" className="flex flex-col items-start gap-3 p-6">
      <AlertTriangle className="size-6 text-danger" aria-hidden />
      <h2 className="text-lg font-bold">{title}</h2>
      <p className="max-w-prose text-ink-soft">{message}</p>
      {requestId && <p className="num text-xs text-ink-soft">Reference: {requestId}</p>}
      {onRetry && retryable && (
        <Button variant="secondary" onClick={onRetry}>
          <RotateCw /> Try again
        </Button>
      )}
    </Card>
  );
}

export function EmptyState({ title, children, action }: { title: string; children?: ReactNode; action?: ReactNode }) {
  return (
    <Card className="flex flex-col items-start gap-3 p-8">
      <Compass className="size-7 text-sal" aria-hidden />
      <h2 className="text-xl font-bold">{title}</h2>
      {children && <div className="max-w-prose text-ink-soft">{children}</div>}
      {action}
    </Card>
  );
}

export function CardListSkeleton({ count = 3 }: { count?: number }) {
  return (
    <div className="space-y-5" aria-busy="true" aria-label="Loading">
      {Array.from({ length: count }, (_, i) => (
        <Card key={i} className="grid gap-5 p-5 md:grid-cols-[220px_1fr]">
          <Skeleton className="h-40 w-full rounded-[10px]" />
          <div className="space-y-3">
            <Skeleton className="h-6 w-2/3" />
            <Skeleton className="h-4 w-1/3" />
            <Skeleton className="h-4 w-full" />
            <Skeleton className="h-4 w-5/6" />
          </div>
        </Card>
      ))}
    </div>
  );
}

export function MockDataNotice({ origin }: { origin: string }) {
  if (origin !== "mock" && origin !== "mixed") return null;
  return (
    <p className="rounded-[var(--radius-control)] bg-[#f6e7c4] px-4 py-2.5 text-sm text-[#6b4a0e]">
      {origin === "mock" ? "You're viewing sample data." : "Some of these results use sample data."} Ratings, reviews
      and photos in sample data are invented for demonstration, not real visitor information.
    </p>
  );
}
