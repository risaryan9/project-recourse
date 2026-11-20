"use client";

import { motion, useReducedMotion, useScroll, useTransform } from "motion/react";

/**
 * The page's light: a few pools of blue that drift very slowly (CSS), with a very light
 * parallax against scroll (transform only, so it stays on the compositor).
 */
export function Ambient() {
  const reduce = useReducedMotion();
  const { scrollYProgress } = useScroll();
  const y = useTransform(scrollYProgress, [0, 1], ["0vh", "-14vh"]);

  return (
    <div className="ambient" aria-hidden="true">
      <motion.div style={reduce ? undefined : { y }} className="absolute inset-0">
        <div className="ambient-pool ambient-pool--a" />
        <div className="ambient-pool ambient-pool--b" />
        <div className="ambient-pool ambient-pool--c" />
      </motion.div>
      <div className="ambient-grain" />
    </div>
  );
}
