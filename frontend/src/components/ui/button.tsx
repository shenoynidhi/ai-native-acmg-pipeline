import * as React from "react"
import { Slot } from "@radix-ui/react-slot"

export interface ButtonProps extends React.ButtonHTMLAttributes<HTMLButtonElement> {
  asChild?: boolean;
  variant?: "default" | "destructive" | "outline" | "secondary" | "ghost" | "link";
  size?: "default" | "sm" | "lg" | "icon";
}

const Button = React.forwardRef<HTMLButtonElement, ButtonProps>(
  ({ className, variant = "default", size = "default", asChild = false, ...props }, ref) => {
    const Comp = asChild ? Slot : "button"

    const variantClass = variant !== "default" ? `button-${variant}` : "";
    const sizeClass = size !== "default" ? `button-${size}` : "";
    const classes = ["button", variantClass, sizeClass, className].filter(Boolean).join(" ");

    return (
      <Comp
        className={classes}
        data-variant={variant}
        data-size={size}
        ref={ref}
        {...props}
      />
    )
  }
)
Button.displayName = "Button"

export { Button }
