const $ = (id) => document.getElementById(id);
const esc = (value) =>
  String(value).replace(
    /[&<>"']/g,
    (c) =>
      ({ "&": "&amp;", "<": "&lt;", ">": "&gt;", '"': "&quot;", "'": "&#39;" })[
        c
      ],
  );
const link = (url, label) =>
  '<a target="_blank" rel="noopener noreferrer" href="' +
  esc(url) +
  '">' +
  esc(label) +
  " ↗</a>";
let workspace,
  run,
  view = "list",
  timer,
  sequence = 0;
const colors = ["#dcf1e4", "#efe4ff", "#ffead2", "#dfecff", "#ffe4e6"];
async function api(path, method = "GET", data) {
  const response = await fetch("/api/v1/" + path, {
    method,
    headers: { "Content-Type": "application/json" },
    body: JSON.stringify(data),
  });
  if (!response.ok)
    throw new Error(
      "Request failed (" + response.status + "). Please try again.",
    );
  return response.json();
}
function message(text) {
  $("message").hidden = !text;
  $("message").textContent = text;
}
function dialog(title, body) {
  $("modal-title").textContent = title;
  $("modal-body").innerHTML = body;
  if (!$("modal").open) $("modal").showModal();
}
$("close").onclick = () => $("modal").close();
function date(value) {
  return new Date(value).toLocaleString(undefined, {
    dateStyle: "medium",
    timeStyle: "short",
  });
}
function vendor(p) {
  const initials = p.name
    .split(" ")
    .slice(0, 2)
    .map((word) => word[0])
    .join("");
  const color = colors[p.name.charCodeAt(0) % colors.length];
  return (
    '<div class="vendor"><span aria-hidden="true" class="avatar" style="--avatar:' +
    color +
    '">' +
    esc(initials) +
    "</span><div><b>" +
    esc(p.name) +
    "</b><small>" +
    esc(p.domain) +
    "</small></div></div>"
  );
}
function status(fact) {
  if (!fact) return '<span class="unknown">Unknown</span>';
  if (fact.review === "dismissed")
    return '<span class="unknown">Dismissed</span>';
  return (
    '<span class="pill">' +
    (fact.review === "approved" ? "Approved" : "Documented") +
    "</span>"
  );
}
function table() {
  if (!run) return;
  const compare = view === "matrix";
  $("list").setAttribute("aria-pressed", String(!compare));
  $("matrix").setAttribute("aria-pressed", String(compare));
  if (!run.profiles.length) {
    $("results").innerHTML =
      '<p class="empty">' +
      (run.status === "failed"
        ? "Discovery failed. Start a new run to retry."
        : "Discovering vendors and retrieving sources…") +
      "</p>";
    return;
  }
  const columns = compare
    ? run.criteria.map((k) => workspace.criteria[k])
    : ["Evidence coverage", "Unverified"];
  const rows = run.profiles
    .map((p, i) => {
      const gaps = run.criteria.length - p.score;
      const meter = run.criteria
        .map((k, j) => '<i class="' + (j < p.score ? "hit" : "") + '"></i>')
        .join("");
      const cells = compare
        ? run.criteria
            .map((k) => "<td>" + status(p.facts[k]) + "</td>")
            .join("")
        : '<td><span class="meter" aria-hidden="true">' +
          meter +
          '</span><span class="mono">' +
          p.score +
          " / " +
          run.criteria.length +
          '</span></td><td class="unknown">' +
          gaps +
          " of " +
          run.criteria.length +
          "</td>";
      return (
        '<tr><td class="index">' +
        String(i + 1).padStart(2, "0") +
        "</td><td>" +
        vendor(p) +
        "</td>" +
        cells +
        '<td><button class="action" data-profile="' +
        i +
        '" aria-label="Inspect ' +
        esc(p.name) +
        '">Inspect ↗</button></td></tr>'
      );
    })
    .join("");
  $("results").innerHTML =
    "<table><thead><tr>" +
    ["Rank", "Vendor", ...columns, "Evidence"]
      .map((c) => "<th>" + esc(c) + "</th>")
      .join("") +
    "</tr></thead><tbody>" +
    rows +
    "</tbody></table>";
  $("results")
    .querySelectorAll("[data-profile]")
    .forEach(
      (b) =>
        (b.onclick = () => inspect(run.profiles[Number(b.dataset.profile)])),
    );
}
function inspect(p) {
  const evidence = run.criteria
    .map((k) => {
      const fact = p.facts[k];
      let detail =
        "<p>The collected sources do not establish this requirement.</p>";
      if (fact) {
        const buttons = ["approved", "dismissed", "unreviewed"]
          .filter((d) => d !== fact.review)
          .map(
            (d) =>
              '<button class="action" data-key="' +
              esc(k) +
              '" data-decision="' +
              d +
              '">' +
              {
                approved: "Approve",
                dismissed: "Dismiss",
                unreviewed: "Reset review",
              }[d] +
              "</button>",
          )
          .join("");
        detail =
          "<blockquote>" +
          esc(fact.quote) +
          '</blockquote><div class="row">' +
          link(fact.url, "Vendor source") +
          link(
            "/api/v1/runs/" +
              run.id +
              "/snapshot?url=" +
              encodeURIComponent(fact.url),
            "Saved copy",
          ) +
          "<small>" +
          esc(date(fact.checked)) +
          "</small>" +
          buttons +
          "</div>";
      }
      return (
        '<div class="section"><div class="row"><h3>' +
        esc(workspace.criteria[k]) +
        "</h3>" +
        status(fact) +
        "</div>" +
        detail +
        "</div>"
      );
    })
    .join("");
  dialog(
    p.name,
    "<p>" +
      esc(p.description) +
      '</p><div class="row">' +
      link(p.site, "Product site") +
      link(p.directory, "Discovery directory") +
      "</div>" +
      evidence,
  );
  $("modal-body")
    .querySelectorAll("[data-decision]")
    .forEach(
      (b) =>
        (b.onclick = async () => {
          try {
            await api("runs/" + run.id + "/review", "PUT", {
              vendor: p.name,
              criterion: b.dataset.key,
              decision: b.dataset.decision,
            });
            await load(run.id);
            inspect(run.profiles.find((v) => v.name === p.name));
          } catch (e) {
            message(e.message);
            $("modal").close();
          }
        }),
    );
}
async function load(id) {
  clearTimeout(timer);
  const token = ++sequence;
  const next = await api("runs/" + id);
  if (token !== sequence) return;
  run = next;
  $("title").textContent = run.title;
  $("chips").innerHTML = run.criteria
    .map((k) => '<span class="chip">' + esc(workspace.criteria[k]) + "</span>")
    .join("");
  const pending = ["queued", "running"].includes(run.status);
  $("candidates").textContent = pending ? "…" : run.profiles.length;
  $("supported").textContent = pending
    ? "…"
    : run.profiles.filter((p) => p.score > 0).length;
  $("requirements").textContent = run.criteria.length;
  $("caption").textContent =
    "Ranked by documented requirements · " +
    date(run.created) +
    " · " +
    run.status;
  message(
    run.issues.length ? "Research issues · " + run.issues.join(" · ") : "",
  );
  table();
  if (pending)
    timer = setTimeout(() => load(id).catch((e) => message(e.message)), 900);
}
$("list").onclick = () => {
  view = "list";
  table();
};
$("matrix").onclick = () => {
  view = "matrix";
  table();
};
$("history").onclick = async () => {
  try {
    workspace = await api("workspace");
    dialog(
      "Run history",
      workspace.runs
        .map(
          (r) =>
            '<button class="history-item" data-id="' +
            esc(r.id) +
            '"><b>' +
            esc(r.title) +
            "</b><small>" +
            esc(date(r.created)) +
            " · " +
            esc(r.status) +
            "</small></button>",
        )
        .join(""),
    );
    $("modal-body")
      .querySelectorAll("[data-id]")
      .forEach(
        (b) =>
          (b.onclick = () => {
            $("modal").close();
            load(b.dataset.id).catch((e) => message(e.message));
          }),
      );
  } catch (e) {
    message(e.message);
  }
};
$("new").onclick = () => {
  if (!workspace) return;
  const selected = run?.criteria || workspace.default;
  dialog(
    "New research",
    '<form id="brief-form"><label>Buyer workflow<input class="field" name="title" required maxlength="100" placeholder="e.g. Load Google Ads into Snowflake daily"></label><button type="button" class="action suggest" id="suggest">Suggest requirements</button><p id="suggest-result" role="status"></p><fieldset><legend>Requirements to compare</legend>' +
      Object.entries(workspace.criteria)
        .map(
          ([k, v]) =>
            '<label class="option"><input type="checkbox" name="criteria" value="' +
            esc(k) +
            '" ' +
            (selected.includes(k) ? "checked" : "") +
            ">" +
            esc(v) +
            "</label>",
        )
        .join("") +
      '</fieldset><p id="form-error" role="alert"></p><button class="primary">Run discovery</button></form>',
  );
  $("suggest").onclick = () => {
    const form = $("brief-form");
    const text = form.elements.title.value;
    const patterns = {
      ads: /google ads|adwords/i,
      snow: /snowflake/i,
      big: /bigquery/i,
      daily: /daily|every (morning|night|day|24 hours)/i,
      fast: /15[- ]min|quarter.hour/i,
      history: /90[- ]days?/i,
      python: /python/i,
    };
    const matches = Object.keys(patterns).filter((k) => patterns[k].test(text));
    if (matches.length) {
      for (const field of form.querySelectorAll('[name="criteria"]')) {
        field.checked = matches.includes(field.value);
      }
    }
    $("suggest-result").textContent = matches.length
      ? "Review the suggested requirements below."
      : "Choose the relevant requirements below.";
  };
  $("brief-form").onsubmit = async (e) => {
    e.preventDefault();
    const data = new FormData(e.target);
    const criteria = data.getAll("criteria"),
      title = data.get("title").trim();
    if (!criteria.length || !title) {
      $("form-error").textContent =
        "Enter a title and select at least one requirement.";
      return;
    }
    const submit = e.target.querySelector("button.primary");
    submit.disabled = true;
    try {
      const { id } = await api("runs", "POST", { title, criteria });
      $("modal").close();
      await load(id);
    } catch (err) {
      $("form-error").textContent = err.message;
      message(err.message);
    } finally {
      submit.disabled = false;
    }
  };
};
(async () => {
  workspace = await api("workspace");
  await load(workspace.runs[0].id);
})().catch((e) => message(e.message));
