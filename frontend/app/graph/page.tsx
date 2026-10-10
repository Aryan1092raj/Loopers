import { redirect } from "next/navigation";

// The course map lives in the workspace at / now; keeps old links working.
export default function GraphPage() {
  redirect("/");
}
