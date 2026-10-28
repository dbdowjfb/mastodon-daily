// Netlify Scheduled Function: reliable daily trigger for the feed build.
//
// Netlify has no native cron -> build, so this function (which runs on a
// schedule) just POSTs the site's build hook, which starts a production build
// that runs scripts/netlify_build.sh.
//
// Setup: Site configuration -> Build & deploy -> Build hooks -> create one,
// then set its URL as the BUILD_HOOK_URL environment variable for the site.

export default async () => {
  const hook = process.env.BUILD_HOOK_URL
  if (!hook) {
    return new Response("BUILD_HOOK_URL is not set", { status: 500 })
  }
  try {
    const res = await fetch(hook, { method: "POST" })
    return new Response(`build hook triggered: ${res.status}`, { status: 200 })
  } catch (err) {
    return new Response(`build hook failed: ${err}`, { status: 502 })
  }
}

export const config = {
  // 22:00 UTC = 06:00 +08, daily.
  schedule: "0 22 * * *",
}
