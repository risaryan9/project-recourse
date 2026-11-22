import type { Metadata } from "next";

import { ArchitectureSlide } from "@/components/slides/architecture-slide";
import { CloseSlide } from "@/components/slides/close-slide";
import { Deck } from "@/components/slides/deck";
import { SafetySlide } from "@/components/slides/safety-slide";

export const metadata: Metadata = {
  title: "Recourse: slides",
  robots: { index: false, follow: false },
};

export default function SlidesPage() {
  return <Deck slides={[<ArchitectureSlide key="a" />, <SafetySlide key="s" />, <CloseSlide key="c" />]} />;
}
