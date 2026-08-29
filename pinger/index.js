export default {
  async scheduled(event, env, ctx) {
    try {
      const res = await fetch("https://cogext.onrender.com/health", {
        method: "GET",
        headers: { "User-Agent": "cogext-pinger/1.0" },
      });
      console.log(`Ping ${res.status} at ${new Date().toISOString()}`);
    } catch (err) {
      console.error("Ping failed:", err.message);
    }
  },
};
