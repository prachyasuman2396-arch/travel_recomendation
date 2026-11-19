import { Slot } from "@radix-ui/react-slot";
import { cva, type VariantProps } from "class-variance-authority";
import type { ComponentProps } from "react";
import { cn } from "@/lib/utils";

const buttonVariants = cva(
  "inline-flex items-center justify-center gap-2 whitespace-nowrap font-semibold transition-colors " +
    "disabled:pointer-events-none disabled:opacity-50 [&_svg]:size-4 [&_svg]:shrink-0",
  {
    variants: {
      variant: {
        primary: "bg-sal text-white hover:bg-sal-deep",
        secondary: "bg-white text-ink ring-1 ring-line hover:bg-mist",
        ghost: "text-ink hover:bg-mist",
        link: "text-monsoon underline-offset-4 hover:underline px-0",
      },
      size: {
        sm: "h-9 rounded-[var(--radius-control)] px-3 text-sm",
        md: "h-11 rounded-[var(--radius-control)] px-5 text-[15px]",
        lg: "h-13 rounded-[var(--radius-control)] px-7 text-base",
      },
    },
    defaultVariants: { variant: "primary", size: "md" },
  },
);

export function Button({
  className,
  variant,
  size,
  asChild = false,
  ...props
}: ComponentProps<"button"> & VariantProps<typeof buttonVariants> & { asChild?: boolean }) {
  const Comp = asChild ? Slot : "button";
  return <Comp className={cn(buttonVariants({ variant, size }), className)} {...props} />;
}
