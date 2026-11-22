import { CaseTimeline } from "@/components/case/case-pages";

// The case loads in the browser (it belongs to this browser's owner cookie), so there is
// no server-rendered shell to navigate to instantly.
export const instant = false;

export default function CaseTimelinePage() {
  return <CaseTimeline />;
}
