/**
 * Record the live AgentCore demo as one continuous camera move.
 *
 * The interaction is established at 1x, clicks are marked with visible ripples,
 * then the camera pushes into the live wall clock. It stays zoomed from there:
 * completed result panels are reached by panning directly between them.
 */

import { execFileSync } from "node:child_process"
import { mkdirSync, rmSync, writeFileSync } from "node:fs"
import { dirname, join, resolve } from "node:path"
import { fileURLToPath } from "node:url"
import { chromium } from "playwright"

const HERE = dirname(fileURLToPath(import.meta.url))
const REPO = resolve(HERE, "../..")
const URL = process.env.DEMO_URL || "http://127.0.0.1:8080/"
const WORK = process.env.DEMO_RECORD_WORK || "/tmp/agentcore-demo-recording"
const FRAMES = join(WORK, "frames")
const OUTPUT = resolve(process.env.DEMO_OUTPUT || join(REPO, "media", "demo-1920.mp4"))
const VIEWPORT = { width: 1920, height: 1200 }
const APPLICATION = process.env.DEMO_APPLICATION || "APP-1004"
const RUN_TIMEOUT_MS = Number(process.env.DEMO_RUN_TIMEOUT_MS || 300_000)
const EASE = "cubic-bezier(0.16, 1, 0.3, 1)"

const wait = (page, ms) => page.waitForTimeout(ms)
const framePath = (index) => join(FRAMES, `${String(index).padStart(6, "0")}.jpg`)

async function installClickEffect(page) {
  await page.addStyleTag({
    content: `
      @keyframes demo-click-ring {
        0% { opacity: 0; transform: translate(-50%, -50%) scale(.28); }
        16% { opacity: 1; }
        72% { opacity: .72; }
        100% { opacity: 0; transform: translate(-50%, -50%) scale(1.55); }
      }
      @keyframes demo-click-dot {
        0%, 100% { transform: translate(-50%, -50%) scale(.78); }
        38% { transform: translate(-50%, -50%) scale(1); }
      }
      .demo-click-ring, .demo-click-dot {
        position: fixed;
        z-index: 2147483647;
        pointer-events: none;
        border-radius: 999px;
      }
      .demo-click-ring {
        width: 48px;
        height: 48px;
        border: 4px solid rgba(37, 106, 191, .96);
        background: rgba(42, 120, 214, .13);
        box-shadow: 0 0 0 2px rgba(255,255,255,.92), 0 5px 20px rgba(37,106,191,.28);
        animation: demo-click-ring 820ms cubic-bezier(.16, 1, .3, 1) forwards;
      }
      .demo-click-dot {
        width: 11px;
        height: 11px;
        background: #256abf;
        border: 2px solid white;
        box-shadow: 0 2px 8px rgba(0,0,0,.28);
        animation: demo-click-dot 360ms ease-out forwards;
      }
    `,
  })
}

async function showClick(page, locator, { beforeMs = 210, afterMs = 520 } = {}) {
  await locator.scrollIntoViewIfNeeded()
  const box = await locator.boundingBox()
  if (!box) throw new Error("Click target has no bounding box")
  const point = { x: box.x + box.width / 2, y: box.y + box.height / 2 }
  await page.mouse.move(point.x, point.y, { steps: 9 })
  await page.evaluate(({ x, y }) => {
    document.querySelectorAll(".demo-click-ring,.demo-click-dot").forEach((node) => node.remove())
    for (const className of ["demo-click-ring", "demo-click-dot"]) {
      const marker = document.createElement("div")
      marker.className = className
      marker.style.left = `${x}px`
      marker.style.top = `${y}px`
      document.body.append(marker)
      marker.addEventListener("animationend", () => marker.remove(), { once: true })
    }
  }, point)
  await wait(page, beforeMs)
  await locator.click()
  await wait(page, afterMs)
}

/** Move directly from the current transform to the next subject without pulling out. */
async function moveCamera(page, text, {
  scale = 1.42,
  durationMs = 1350,
  top = 0.095,
  scope = "card",
} = {}) {
  const result = await page.evaluate(({ text, scale, durationMs, top, scope, ease }) => {
    const exact = (node) => (node.textContent || "").trim().toLowerCase() === text.toLowerCase()
    // Prefer actual panel titles. This matters for "Cost", which also appears
    // dozens of times as a small metric label inside specialist cards.
    const titled = [...document.querySelectorAll("[data-slot='card-title'],h1,h2,h3")]
      .filter(exact)
    const fallback = [...document.querySelectorAll("div,span")].filter(exact)
    const label = [...titled, ...fallback].find((node) => {
      const rect = node.getBoundingClientRect()
      return rect.width > 2 && rect.height > 2
    })
    if (!label) return null

    const subject = scope === "card" ? (label.closest("[data-slot='card']") || label) : label
    const state = window.__demoCamera || { x: 0, y: 0, scale: 1 }
    const rect = subject.getBoundingClientRect()
    const layout = {
      x: (rect.left - state.x) / state.scale,
      y: (rect.top - state.y) / state.scale,
      width: rect.width / state.scale,
    }
    const vw = window.innerWidth
    const vh = window.innerHeight
    const pageWidth = Math.max(document.documentElement.clientWidth, document.body.offsetWidth)
    const pageHeight = Math.max(document.documentElement.offsetHeight, document.body.offsetHeight)
    let x = vw / 2 - (layout.x + layout.width / 2) * scale
    let y = vh * top - layout.y * scale
    x = Math.min(0, Math.max(vw - pageWidth * scale, x))
    y = Math.min(0, Math.max(vh - pageHeight * scale, y))

    const html = document.documentElement
    html.style.transformOrigin = "0 0"
    html.style.willChange = "transform"
    html.style.transition = `transform ${durationMs}ms ${ease}`
    html.style.transform = `translate3d(${x}px, ${y}px, 0) scale(${scale})`
    window.__demoCamera = { x, y, scale }
    return { x: Math.round(x), y: Math.round(y), scale }
  }, { text, scale, durationMs, top, scope, ease: EASE })

  if (!result) throw new Error(`Camera subject not found: ${text}`)
  console.log(`camera -> ${text} (${result.scale}x, ${result.x}, ${result.y})`)
  await wait(page, durationMs + 120)
}

async function waitForRun(page) {
  const deadline = Date.now() + RUN_TIMEOUT_MS
  let lastDone = -1
  let lastHeartbeat = 0
  while (Date.now() < deadline) {
    const status = await page.evaluate(() => {
      const body = document.body.innerText
      const returned = body.match(/(\d+) of 8 returned\./)
      const clockLabel = [...document.querySelectorAll("div")]
        .find((node) => (node.textContent || "").trim().toLowerCase() === "wall clock")
      const clock = clockLabel?.nextElementSibling?.textContent?.trim() || "—"
      const validated = body.includes("21 keys, contract-validated")
      const running = [...document.querySelectorAll("button")]
        .some((button) => (button.textContent || "").trim() === "Running")
      return {
        done: returned ? Number(returned[1]) : 0,
        clock,
        complete: validated && !running,
        error: body.includes("The stream closed before a run_completed frame arrived"),
      }
    })
    if (status.done !== lastDone || Date.now() - lastHeartbeat > 10_000) {
      console.log(`run: ${status.done}/8 specialists, wall clock ${status.clock}`)
      lastDone = status.done
      lastHeartbeat = Date.now()
    }
    if (status.error) throw new Error("The run stream closed before completion")
    if (status.complete) {
      console.log("run: 8/8 specialists and master adjudication complete")
      return
    }
    await wait(page, 700)
  }
  throw new Error(`Run did not complete within ${RUN_TIMEOUT_MS / 1000}s`)
}

function writeConcat(frameTimes, stoppedAt) {
  if (frameTimes.length < 2) throw new Error(`Only ${frameTimes.length} screencast frames arrived`)
  const lines = ["ffconcat version 1.0"]
  for (let index = 0; index < frameTimes.length; index += 1) {
    const next = frameTimes[index + 1] ?? stoppedAt
    const duration = Math.max(1 / 120, (next - frameTimes[index]) / 1000)
    lines.push(`file '${framePath(index)}'`)
    lines.push(`duration ${duration.toFixed(6)}`)
  }
  lines.push(`file '${framePath(frameTimes.length - 1)}'`)
  const concatPath = join(WORK, "frames.ffconcat")
  writeFileSync(concatPath, `${lines.join("\n")}\n`)
  return concatPath
}

function encode(concatPath) {
  mkdirSync(dirname(OUTPUT), { recursive: true })
  console.log(`encoding -> ${OUTPUT}`)
  execFileSync("ffmpeg", [
    "-y", "-hide_banner", "-loglevel", "warning",
    "-f", "concat", "-safe", "0", "-i", concatPath,
    "-vf", "fps=30,scale=in_range=pc:out_range=tv,format=yuv420p",
    "-c:v", "libx264", "-preset", "medium", "-crf", "18",
    "-profile:v", "high", "-color_range", "tv", "-movflags", "+faststart", OUTPUT,
  ], { stdio: "inherit" })
}

async function main() {
  rmSync(WORK, { recursive: true, force: true })
  mkdirSync(FRAMES, { recursive: true })
  const browser = await chromium.launch({ headless: true, args: ["--hide-scrollbars"] })
  const context = await browser.newContext({
    viewport: VIEWPORT,
    colorScheme: "light",
    deviceScaleFactor: 1,
    reducedMotion: "no-preference",
  })
  const page = await context.newPage()
  const errors = []
  page.on("pageerror", (error) => errors.push(String(error)))
  page.on("console", (message) => {
    if (message.type() === "error") errors.push(message.text())
  })

  const cdp = await context.newCDPSession(page)
  const frameTimes = []
  const pending = new Set()
  let frameIndex = 0
  cdp.on("Page.screencastFrame", (event) => {
    const task = (async () => {
      const index = frameIndex++
      frameTimes[index] = performance.now()
      writeFileSync(framePath(index), Buffer.from(event.data, "base64"))
      await cdp.send("Page.screencastFrameAck", { sessionId: event.sessionId })
    })().catch((error) => errors.push(`screencast: ${error}`))
    pending.add(task)
    task.finally(() => pending.delete(task))
  })

  try {
    console.log(`opening ${URL}`)
    await page.goto(URL, { waitUntil: "networkidle", timeout: 30_000 })
    await installClickEffect(page)
    await page.evaluate(() => window.scrollTo(0, 0))
    await cdp.send("Page.startScreencast", {
      format: "jpeg", quality: 92, everyNthFrame: 1,
      maxWidth: VIEWPORT.width, maxHeight: VIEWPORT.height,
    })

    await wait(page, 2100)
    const applicationSelect = page.locator("#application-select")
    await showClick(page, applicationSelect, { afterMs: 850 })
    const option = page.getByRole("option", { name: APPLICATION, exact: true })
    await option.waitFor({ state: "visible", timeout: 5000 })
    await wait(page, 500)
    await showClick(page, option, { afterMs: 900 })
    await wait(page, 1250)

    const runButton = page.getByRole("button", { name: "Run adjudication", exact: true })
    await showClick(page, runButton, { beforeMs: 230, afterMs: 620 })
    console.log(`run started for ${APPLICATION}`)

    // Preserve the full-page context briefly after the click, then push in once.
    await wait(page, 1500)
    await moveCamera(page, "Wall clock", {
      // Keep the full timer and its value comfortably inside the right edge.
      // The later result moves push further in to 1.42x; there is no pull-out.
      scale: 1.28, durationMs: 1450, top: 0.11, scope: "card",
    })
    await waitForRun(page)
    await wait(page, 2200)

    // Stay zoomed and pan directly between fully populated result panels.
    const beats = [
      ["Measured latency by agent", 3200],
      ["Specialist analyses", 3300],
      ["Master adjudication", 4700],
      ["Signal layer", 3200],
      ["Cost", 4500],
    ]
    for (const [subject, holdMs] of beats) {
      await moveCamera(page, subject, {
        scale: 1.42, durationMs: 1400, top: 0.085, scope: "card",
      })
      await wait(page, holdMs)
    }

    await wait(page, 900)
    await cdp.send("Page.stopScreencast")
    const stoppedAt = performance.now()
    await wait(page, 500)
    await Promise.allSettled([...pending])
    console.log(`frames: ${frameTimes.length}`)
    if (errors.length > 0) console.log("browser errors:", errors.slice(0, 5))
    encode(writeConcat(frameTimes, stoppedAt))
  } finally {
    await context.close()
    await browser.close()
  }
}

main().catch((error) => {
  console.error(error)
  process.exitCode = 1
})
