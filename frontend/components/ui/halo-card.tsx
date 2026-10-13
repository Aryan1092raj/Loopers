"use client"

/**
 * Composable card matching `HaloSearchInput` / `HaloButtonPolished`:
 * animated gradient rim, frosted fill, layered shadow, fine-hover polish.
 *
 * @example
 * <HaloCard aria-label="Example">
 *   <HaloCardHeader>
 *     <HaloCardTitle>Title</HaloCardTitle>
 *     <HaloCardDescription>Body copy.</HaloCardDescription>
 *   </HaloCardHeader>
 *   <HaloCardContent>Optional main area.</HaloCardContent>
 *   <HaloCardFooter>
 *     <HaloCardButton variant="secondary">Cancel</HaloCardButton>
 *     <HaloCardButton variant="primary">Continue</HaloCardButton>
 *   </HaloCardFooter>
 * </HaloCard>
 */
import {
  forwardRef,
  useEffect,
  useMemo,
  useState,
  type ComponentProps,
  type HTMLAttributes,
  type ReactNode,
} from "react"
import { Button as ButtonPrimitive } from "@base-ui/react/button"
import {
  motion,
  useMotionValue,
  useReducedMotion,
  useTransform,
} from "motion/react"

import { cn } from "@/lib/utils"

const paddingClass = {
  none: "",
  sm: "p-4",
  md: "p-6",
  lg: "p-8",
} as const

const BORDER_TRAIL_LOOP_MS = 5250

/** Moving rim highlight along the card edge (mask = border band only). Phase persists when hover/focus stops. */
function HaloCardBorderTrail({ active }: { active: boolean }) {
  const progress = useMotionValue(0)
  const offsetDistance = useTransform(progress, (v: number) => {
    const wrapped = ((v % 100) + 100) % 100
    return `${wrapped}%`
  })

  useEffect(() => {
    if (!active) {
      return
    }
    let rafId = 0
    let last = performance.now()
    const tick = (now: number) => {
      const dt = now - last
      last = now
      const next = progress.get() + (dt / BORDER_TRAIL_LOOP_MS) * 100
      progress.set(((next % 100) + 100) % 100)
      rafId = requestAnimationFrame(tick)
    }
    rafId = requestAnimationFrame(tick)
    return () => cancelAnimationFrame(rafId)
  }, [active, progress])

  const pathCornerRadiusPx = 16
  const trailLengthPx = 184
  const trailThicknessPx = 32

  const trailBox = {
    height: trailThicknessPx,
    offsetPath: `rect(0 auto auto 0 round ${pathCornerRadiusPx}px)`,
    width: trailLengthPx,
  } as const

  /** Feathered ends + same hue family as `borderGlowConfig` so it reads as one rim system. */
  const ambientGradient =
    "linear-gradient(90deg, transparent 0%, rgba(99,102,241,0) 6%, rgba(99,102,241,0.3) 24%, rgba(129,140,248,0.42) 50%, rgba(148,163,184,0.34) 76%, rgba(148,163,184,0) 94%, transparent 100%)"

  const coreGradient =
    "linear-gradient(90deg, transparent 0%, rgba(99,102,241,0.6) 20%, rgba(165,180,252,0.9) 50%, rgba(148,163,184,0.65) 80%, transparent 100%)"

  return (
    <div
      className={cn(
        "pointer-events-none absolute inset-0 rounded-[inherit] border border-transparent [mask-clip:padding-box,border-box] [mask-composite:intersect] [mask-image:linear-gradient(transparent,transparent),linear-gradient(#000,#000)]",
        active ? "visible opacity-100" : "invisible opacity-0"
      )}
    >
      {/* Wide soft bloom — ties into frosted card edge */}
      <motion.div
        className="absolute opacity-95 dark:opacity-[0.98]"
        style={{
          ...trailBox,
          filter: "blur(12px)",
          offsetDistance,
        }}
      >
        <div
          className="size-full rounded-[999px]"
          style={{
            background: ambientGradient,
            boxShadow:
              "0 0 22px rgba(99,102,241,0.2), 0 0 44px rgba(148,163,184,0.15), 0 0 2px rgba(255,255,255,0.12)",
          }}
        />
      </motion.div>
      {/* Brighter core — higher-opacity read without a hard capsule edge */}
      <motion.div
        className="absolute dark:opacity-[0.94]"
        style={{
          ...trailBox,
          filter: "blur(4px)",
          offsetDistance,
        }}
      >
        <div
          className="size-full rounded-[999px]"
          style={{
            background: coreGradient,
            boxShadow:
              "0 0 14px rgba(99,102,241,0.28), 0 0 28px rgba(148,163,184,0.25), inset 0 0 1px rgba(255,255,255,0.22)",
          }}
        />
      </motion.div>
    </div>
  )
}

function borderGlowConfig(translucent: boolean, rimEmphasis: boolean) {
  let idleOpacity: number
  if (translucent) {
    idleOpacity = rimEmphasis ? 0.92 : 0.72
  } else {
    idleOpacity = rimEmphasis ? 0.78 : 0.5
  }
  return {
    opacityIdle: idleOpacity,
    // the site's own indigo and slate, in place of the original pink, purple and cyan
    primaryBg: "linear-gradient(90deg, #6366f1, #818cf8, #94a3b8, #4f46e5)",
    secondaryBg: "linear-gradient(90deg, #a5b4fc, #c7d2fe, #94a3b8)",
    tertiaryBg: "linear-gradient(90deg, #4f46e5, #818cf8, #cbd5e1)",
    primaryLeft: ["-5%", "75%", "-5%"],
    secondaryLeft: ["65%", "10%", "65%"],
    tertiaryLeft: ["25%", "55%", "25%"],
    durationMain: 6,
    durationSec: 5,
    durationTer: 4,
  }
}

type BorderGlowState = ReturnType<typeof borderGlowConfig>

function GradientRimPillBlobs({
  borderGlow,
  reduceMotion,
}: {
  borderGlow: BorderGlowState
  reduceMotion: boolean
}) {
  return (
    <div className="pointer-events-none absolute inset-0 overflow-hidden rounded-full">
      <motion.div
        animate={{
          opacity: reduceMotion
            ? borderGlow.opacityIdle * 0.82
            : borderGlow.opacityIdle,
        }}
        className="absolute inset-x-0 bottom-0 h-1/2"
        transition={{ duration: 0.25, ease: "easeOut" }}
      >
        <motion.div
          animate={{
            left: borderGlow.primaryLeft,
          }}
          className="-bottom-5 absolute h-20 w-56 blur-2xl"
          style={{
            background: borderGlow.primaryBg,
          }}
          transition={{
            duration: reduceMotion ? 0.01 : borderGlow.durationMain,
            repeat: reduceMotion ? 0 : Number.POSITIVE_INFINITY,
            ease: "easeInOut",
          }}
        />
        <motion.div
          animate={{
            left: borderGlow.secondaryLeft,
          }}
          className="-bottom-4 absolute h-16 w-40 blur-xl"
          style={{
            background: borderGlow.secondaryBg,
          }}
          transition={{
            duration: reduceMotion ? 0.01 : borderGlow.durationSec,
            repeat: reduceMotion ? 0 : Number.POSITIVE_INFINITY,
            ease: "easeInOut",
          }}
        />
        <motion.div
          animate={{
            left: borderGlow.tertiaryLeft,
          }}
          className="-bottom-3 absolute h-14 w-32 blur-lg"
          style={{
            background: borderGlow.tertiaryBg,
          }}
          transition={{
            duration: reduceMotion ? 0.01 : borderGlow.durationTer,
            repeat: reduceMotion ? 0 : Number.POSITIVE_INFINITY,
            ease: "easeInOut",
          }}
        />
      </motion.div>
    </div>
  )
}

function fillBackgroundFor(
  translucent: boolean,
  interactive: boolean,
  rimEmphasis: boolean
) {
  if (translucent) {
    if (interactive && rimEmphasis) {
      return "linear-gradient(to bottom, color-mix(in oklch, var(--card) 88%, transparent) 0%, color-mix(in oklch, var(--card) 74%, transparent) 100%)"
    }
    return "linear-gradient(to bottom, color-mix(in oklch, var(--card) 82%, transparent) 0%, color-mix(in oklch, var(--card) 70%, transparent) 100%)"
  }
  if (interactive && rimEmphasis) {
    return "linear-gradient(to bottom, var(--card) 0%, color-mix(in oklch, var(--card) 90%, var(--muted)) 100%)"
  }
  return "linear-gradient(to bottom, var(--card) 0%, color-mix(in oklch, var(--card) 94%, var(--muted)) 100%)"
}

export interface HaloCardProps extends ComponentProps<"div"> {
  children: ReactNode
  /** Frosted inner surface so gradient blobs read through. */
  translucent?: boolean
  /** Hover + rim emphasis on the animated edge. */
  interactive?: boolean
  /** Padding inside the card surface. */
  padding?: keyof typeof paddingClass
  /** CSS background for the surface, replacing the default fill. Keep it partly transparent so the moving glow still reads through. */
  fill?: string
}

const HaloCard = forwardRef<HTMLDivElement, HaloCardProps>(function HaloCard(
  {
    children,
    className,
    translucent = true,
    interactive = true,
    padding = "md",
    fill,
    "aria-label": ariaLabel = "Card",
    onBlur,
    onFocus,
    onMouseEnter,
    onMouseLeave,
    ...props
  },
  ref
) {
  const [isHovered, setIsHovered] = useState(false)
  const [isFocusedWithin, setIsFocusedWithin] = useState(false)
  const reduceMotion = useReducedMotion() ?? false

  const rimEmphasis = interactive && (isHovered || isFocusedWithin)

  const borderGlow = useMemo(
    () => borderGlowConfig(translucent, rimEmphasis),
    [translucent, rimEmphasis]
  )

  const fillBackground = useMemo(
    () => fillBackgroundFor(translucent, interactive, rimEmphasis),
    [translucent, interactive, rimEmphasis]
  )

  const showTrailChrome = interactive && !reduceMotion
  const trailActive = isHovered || isFocusedWithin

  return (
    // biome-ignore lint/a11y/noNoninteractiveElementInteractions: hover drives animated rim; focusable controls render in children.
    // biome-ignore lint/a11y/useSemanticElements: not a form group; fieldset would be incorrect for layout chrome.
    <div
      aria-label={ariaLabel}
      className={cn("relative w-full max-w-full", className)}
      data-slot="halo-card"
      onBlur={(e) => {
        if (!e.currentTarget.contains(e.relatedTarget as Node | null)) {
          setIsFocusedWithin(false)
        }
        onBlur?.(e)
      }}
      onFocus={(e) => {
        if (interactive) {
          setIsFocusedWithin(true)
        }
        onFocus?.(e)
      }}
      onMouseEnter={(e) => {
        if (interactive) {
          setIsHovered(true)
        }
        onMouseEnter?.(e)
      }}
      onMouseLeave={(e) => {
        setIsHovered(false)
        onMouseLeave?.(e)
      }}
      ref={ref}
      role="group"
      {...props}
    >
      <div className="relative h-full overflow-clip rounded-2xl p-px">
        <div className="pointer-events-none absolute inset-0 z-0 overflow-hidden rounded-2xl">
          <motion.div
            animate={{
              opacity: reduceMotion
                ? borderGlow.opacityIdle * 0.82
                : borderGlow.opacityIdle,
            }}
            className="absolute inset-x-0 bottom-0 h-[58%]"
            transition={{ duration: 0.25, ease: "easeOut" }}
          >
            {/* Each blob slides inside a full-width wrapper by `x` (a percentage of the card width): transform, not `left`, so no layout work per frame. */}
            <motion.div
              animate={{
                x: borderGlow.primaryLeft,
              }}
              className="-bottom-10 absolute inset-x-0 h-32 will-change-transform"
              transition={{
                duration: reduceMotion ? 0.01 : borderGlow.durationMain,
                repeat: reduceMotion ? 0 : Number.POSITIVE_INFINITY,
                ease: "easeInOut",
              }}
            >
              <div
                className="h-32 w-88 blur-2xl"
                style={{ background: borderGlow.primaryBg }}
              />
            </motion.div>
            <motion.div
              animate={{
                x: borderGlow.secondaryLeft,
              }}
              className="-bottom-7 absolute inset-x-0 h-24 will-change-transform"
              transition={{
                duration: reduceMotion ? 0.01 : borderGlow.durationSec,
                repeat: reduceMotion ? 0 : Number.POSITIVE_INFINITY,
                ease: "easeInOut",
              }}
            >
              <div
                className="h-24 w-52 blur-2xl"
                style={{ background: borderGlow.secondaryBg }}
              />
            </motion.div>
            <motion.div
              animate={{
                x: borderGlow.tertiaryLeft,
              }}
              className="-bottom-5 absolute inset-x-0 h-20 will-change-transform"
              transition={{
                duration: reduceMotion ? 0.01 : borderGlow.durationTer,
                repeat: reduceMotion ? 0 : Number.POSITIVE_INFINITY,
                ease: "easeInOut",
              }}
            >
              <div
                className="h-20 w-44 blur-xl"
                style={{ background: borderGlow.tertiaryBg }}
              />
            </motion.div>
          </motion.div>
        </div>

        <div
          className={cn(
            // overflow-hidden: a child's glow (the beam round the composer) must not spill past the card and change the page height
            "relative z-0 flex h-full flex-col gap-6 overflow-hidden rounded-[15px] border border-border/90",
            "shadow-[0_1px_0_rgba(255,255,255,0.1)_inset,0_1px_2px_rgba(0,0,0,0.04),0_4px_14px_rgba(0,0,0,0.05)]",
            "dark:border-border dark:shadow-[0_1px_0_rgba(255,255,255,0.06)_inset,0_1px_2px_rgba(0,0,0,0.35),0_4px_20px_rgba(0,0,0,0.35)]",
            "outline-none transition-[background-color,backdrop-filter,border-color,box-shadow] duration-200 ease-out",
            interactive &&
              "fine-hover:hover:border-ring/50 fine-hover:hover:shadow-[0_1px_0_rgba(255,255,255,0.14)_inset,0_1px_2px_rgba(0,0,0,0.09),0_4px_14px_rgba(0,0,0,0.11)]",
            interactive &&
              "dark:fine-hover:hover:border-zinc-500/60 dark:fine-hover:hover:shadow-[0_1px_0_rgba(255,255,255,0.08)_inset,0_1px_2px_rgba(0,0,0,0.42),0_4px_20px_rgba(0,0,0,0.48)]",
            interactive &&
              "focus-within:border-ring/70 focus-within:shadow-[0_1px_0_rgba(255,255,255,0.14)_inset,0_1px_2px_rgba(0,0,0,0.09),0_4px_14px_rgba(0,0,0,0.11)]",
            interactive &&
              "dark:focus-within:border-zinc-500/55 dark:focus-within:shadow-[0_1px_0_rgba(255,255,255,0.08)_inset,0_1px_2px_rgba(0,0,0,0.42),0_4px_20px_rgba(0,0,0,0.48)]",
            // no backdrop filter on purpose: the blobs are already soft, and filtering a large moving backdrop every frame makes it shimmer
            padding !== "none" && paddingClass[padding]
          )}
          data-slot="halo-card-surface"
          style={{ background: fill ?? fillBackground }}
        >
          {children}
        </div>

        {showTrailChrome ? (
          <div className="pointer-events-none absolute inset-0 z-10 rounded-2xl">
            <HaloCardBorderTrail active={trailActive} />
          </div>
        ) : null}
      </div>
    </div>
  )
})

HaloCard.displayName = "HaloCard"

function HaloCardHeader({ className, ...props }: ComponentProps<"div">) {
  return (
    <div
      className={cn(
        "grid auto-rows-min items-start gap-x-4 gap-y-1.5",
        "has-[>[data-slot=halo-card-action]]:grid-cols-[1fr_auto]",
        "has-[>[data-slot=halo-card-description]]:grid-rows-[auto_auto]",
        className
      )}
      data-slot="halo-card-header"
      {...props}
    />
  )
}
HaloCardHeader.displayName = "HaloCardHeader"

const HaloCardTitle = forwardRef<HTMLHeadingElement, ComponentProps<"h3">>(
  function HaloCardTitle({ className, ...props }, ref) {
    return (
      <h3
        className={cn(
          "col-start-1 row-start-1 text-balance font-semibold text-foreground text-lg leading-tight tracking-tight antialiased",
          className
        )}
        data-slot="halo-card-title"
        ref={ref}
        {...props}
      />
    )
  }
)
HaloCardTitle.displayName = "HaloCardTitle"

const HaloCardDescription = forwardRef<
  HTMLParagraphElement,
  ComponentProps<"p">
>(function HaloCardDescription({ className, ...props }, ref) {
  return (
    <p
      className={cn(
        "col-start-1 row-start-2 text-pretty text-muted-foreground text-sm leading-relaxed",
        className
      )}
      data-slot="halo-card-description"
      ref={ref}
      {...props}
    />
  )
})
HaloCardDescription.displayName = "HaloCardDescription"

function HaloCardAction({ className, ...props }: ComponentProps<"div">) {
  return (
    <div
      className={cn(
        "col-start-2 row-span-2 row-start-1 self-start justify-self-end",
        className
      )}
      data-slot="halo-card-action"
      {...props}
    />
  )
}
HaloCardAction.displayName = "HaloCardAction"

function HaloCardContent({
  className,
  ...props
}: HTMLAttributes<HTMLDivElement>) {
  return (
    <div
      className={cn("flex min-h-0 flex-col gap-3", className)}
      data-slot="halo-card-content"
      {...props}
    />
  )
}
HaloCardContent.displayName = "HaloCardContent"

function HaloCardFooter({ className, ...props }: ComponentProps<"div">) {
  return (
    <div
      className={cn("flex flex-wrap items-center justify-end gap-2", className)}
      data-slot="halo-card-footer"
      {...props}
    />
  )
}
HaloCardFooter.displayName = "HaloCardFooter"

export interface HaloCardButtonProps
  extends Omit<ComponentProps<typeof ButtonPrimitive>, "children"> {
  children: ReactNode
  /**
   * `primary` — animated gradient rim + frosted fill (matches card chrome).
   * `secondary` — muted surface, high-contrast `text-foreground`, subtle gradient ring (e.g. Cancel).
   */
  variant?: "primary" | "secondary"
  size?: "default" | "icon"
}

const HaloCardButton = forwardRef<HTMLButtonElement, HaloCardButtonProps>(
  function HaloCardButton(
    {
      children,
      className,
      variant = "primary",
      size = "default",
      disabled,
      onFocus,
      onBlur,
      onMouseEnter,
      onMouseLeave,
      style,
      type,
      ...props
    },
    ref
  ) {
    const [isHovered, setIsHovered] = useState(false)
    const [isFocused, setIsFocused] = useState(false)
    const reduceMotion = useReducedMotion() ?? false

    const rimEmphasis = isHovered || isFocused

    const borderGlow = useMemo(
      () => borderGlowConfig(true, rimEmphasis),
      [rimEmphasis]
    )

    const fillBackground = useMemo(
      () => fillBackgroundFor(true, true, rimEmphasis),
      [rimEmphasis]
    )

    if (variant === "secondary") {
      return (
        <div
          className={cn("relative inline-flex max-w-full", className)}
          data-slot="halo-card-button"
          data-variant="secondary"
        >
          <div
            className={cn(
              "rounded-full p-px transition-opacity duration-200 ease-out",
              "fine-hover:opacity-100 opacity-95",
              "[background:linear-gradient(135deg,rgba(99,102,241,0.4),rgba(129,140,248,0.3),rgba(148,163,184,0.38))]",
              "dark:[background:linear-gradient(135deg,rgba(129,140,248,0.32),rgba(165,180,252,0.28),rgba(148,163,184,0.35))]"
            )}
          >
            <ButtonPrimitive
              className={cn(
                "relative flex min-w-0 select-none items-center justify-center rounded-full",
                "border border-border/90 bg-muted font-medium text-foreground text-sm tracking-tight antialiased",
                "shadow-[0_1px_0_rgba(255,255,255,0.1)_inset,0_1px_2px_rgba(0,0,0,0.06)]",
                "dark:border-border dark:bg-muted dark:text-foreground",
                "fine-hover:hover:border-ring/50 fine-hover:hover:bg-accent fine-hover:hover:text-accent-foreground",
                "outline-none transition-[background-color,border-color,box-shadow,color,transform] duration-200 ease-out",
                "focus-visible:border-ring/70 focus-visible:ring-2 focus-visible:ring-ring/30 dark:focus-visible:border-zinc-500/45",
                "enabled:active:scale-[0.96] motion-reduce:enabled:active:scale-100",
                "disabled:pointer-events-none disabled:opacity-50",
                size === "icon"
                  ? "size-11 p-0 [&_svg]:shrink-0"
                  : "min-h-11 px-6 py-2.5"
              )}
              disabled={disabled}
              onBlur={(e) => {
                setIsFocused(false)
                onBlur?.(e)
              }}
              onFocus={(e) => {
                setIsFocused(true)
                onFocus?.(e)
              }}
              onMouseEnter={(e) => {
                setIsHovered(true)
                onMouseEnter?.(e)
              }}
              onMouseLeave={(e) => {
                setIsHovered(false)
                onMouseLeave?.(e)
              }}
              ref={ref}
              type={type ?? "button"}
              {...props}
            >
              {children}
            </ButtonPrimitive>
          </div>
        </div>
      )
    }

    return (
      <div
        className={cn("relative inline-flex max-w-full", className)}
        data-slot="halo-card-button"
        data-variant="primary"
      >
        <div className="relative rounded-full p-px">
          <GradientRimPillBlobs
            borderGlow={borderGlow}
            reduceMotion={reduceMotion}
          />
          <ButtonPrimitive
            className={cn(
              "relative flex min-w-0 select-none items-center justify-center rounded-full",
              "border border-border/90",
              "shadow-[0_1px_0_rgba(255,255,255,0.1)_inset,0_1px_2px_rgba(0,0,0,0.04),0_4px_14px_rgba(0,0,0,0.05)]",
              "dark:border-border dark:shadow-[0_1px_0_rgba(255,255,255,0.06)_inset,0_1px_2px_rgba(0,0,0,0.35),0_4px_20px_rgba(0,0,0,0.35)]",
              "outline-none transition-[transform,background-color,backdrop-filter,border-color,box-shadow] duration-200 ease-out",
              "fine-hover:hover:border-ring/50 fine-hover:hover:shadow-[0_1px_0_rgba(255,255,255,0.12)_inset,0_2px_6px_rgba(0,0,0,0.06),0_8px_24px_rgba(0,0,0,0.08)]",
              "dark:fine-hover:hover:border-zinc-500/60 dark:fine-hover:hover:shadow-[0_1px_0_rgba(255,255,255,0.08)_inset,0_4px_12px_rgba(0,0,0,0.45),0_12px_32px_rgba(0,0,0,0.45)]",
              "focus-visible:border-ring/70 focus-visible:ring-2 focus-visible:ring-ring/30 dark:focus-visible:border-zinc-500/45",
              "enabled:active:scale-[0.96] motion-reduce:enabled:active:scale-100",
              "disabled:pointer-events-none disabled:opacity-50",
              "backdrop-blur-xl backdrop-saturate-150",
              "font-medium text-foreground text-sm tracking-tight antialiased",
              size === "icon"
                ? "size-11 p-0 [&_svg]:shrink-0"
                : "min-h-11 gap-2 px-6 py-2.5"
            )}
            disabled={disabled}
            onBlur={(e) => {
              setIsFocused(false)
              onBlur?.(e)
            }}
            onFocus={(e) => {
              setIsFocused(true)
              onFocus?.(e)
            }}
            onMouseEnter={(e) => {
              setIsHovered(true)
              onMouseEnter?.(e)
            }}
            onMouseLeave={(e) => {
              setIsHovered(false)
              onMouseLeave?.(e)
            }}
            ref={ref}
            style={{
              ...style,
              background: fillBackground,
            }}
            type={type ?? "button"}
            {...props}
          >
            {children}
          </ButtonPrimitive>
        </div>
      </div>
    )
  }
)

HaloCardButton.displayName = "HaloCardButton"

/** @deprecated Use `HaloCard` — alias kept for existing imports. */
const HaloCardPolished = HaloCard

/** @deprecated Use `HaloCardProps` */
export type HaloCardPolishedProps = HaloCardProps

export {
  HaloCard,
  HaloCardAction,
  HaloCardButton,
  HaloCardContent,
  HaloCardDescription,
  HaloCardFooter,
  HaloCardHeader,
  /** @deprecated Use `HaloCard` */
  HaloCardPolished,
  HaloCardTitle,
}
