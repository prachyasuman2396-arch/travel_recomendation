import { useState } from "react";
import { categoryMeta } from "@/lib/categories";
import { cn } from "@/lib/utils";

/**
 * Shows a permitted Flickr photo when one exists; otherwise a category-tinted
 * contour panel. We never substitute stock imagery that isn't of the place.
 */
export function DestinationImage({ url, category, name, className }: {
  url: string | null | undefined;
  category: string;
  name: string;
  className?: string;
}) {
  const [failed, setFailed] = useState(false);
  const meta = categoryMeta(category);
  const Icon = meta.icon;
  if (url && !failed) {
    return (
      <img
        src={url}
        alt={`Photo near ${name}`}
        loading="lazy"
        onError={() => setFailed(true)}
        className={cn("h-full w-full rounded-[10px] object-cover", className)}
      />
    );
  }
  return (
    <div
      role="img"
      aria-label={`${meta.label} destination, no photo available`}
      className={cn("contours relative grid h-full min-h-32 w-full place-items-center overflow-hidden rounded-[10px]",
        className)}
      style={{ backgroundColor: meta.tint, color: "white" }}
    >
      <Icon className="size-10 opacity-90" aria-hidden />
    </div>
  );
}
