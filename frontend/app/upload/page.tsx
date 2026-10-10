import { redirect } from "next/navigation";

// Upload lives in the workspace at / now; keeps old links working.
export default function UploadPage() {
  redirect("/");
}
