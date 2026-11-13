"use strict";

/* ==========================================================
   GLOBAL STATE
========================================================== */

let currentUser = null;
let projects = [];
let dashboardData = null;
let activeProposalId = null;
let activeChatTimer = null;
let currentTab = "overview";
let confirmResolver = null;
let chatLastMessageId = 0;
let activeRatingProfileId = null;
let activeRatingEligibleProjects = [];
let ratingDraftScore = 0;

const $ = id => document.getElementById(id);


/* ==========================================================
   API
========================================================== */

async function api(url, method="GET", data=null) {
    const options = {
        method,
        credentials:"same-origin",
        headers:{}
    };

    if (data !== null) {
        options.headers["Content-Type"] = "application/json";
        options.body = JSON.stringify(data);
    }

    const response = await fetch(url, options);
    let result;

    try {
        result = await response.json();
    } catch {
        throw new Error("پاسخ سرور قابل خواندن نیست.");
    }

    if (!response.ok) {
        throw new Error(result.error || "خطایی رخ داده است.");
    }

    return result;
}


/* ==========================================================
   UI HELPERS
========================================================== */

function escapeHtml(value) {
    return String(value ?? "").replace(/[&<>"']/g, char => ({
        "&":"&amp;",
        "<":"&lt;",
        ">":"&gt;",
        '"':"&quot;",
        "'":"&#039;"
    })[char]);
}

function numberFa(value) {
    return Number(value || 0).toLocaleString("fa-IR");
}

function toast(message, type="success") {
    const el = document.createElement("div");
    el.className = "toast " + type;
    el.textContent = message;
    $("toastContainer").appendChild(el);

    setTimeout(() => {
        el.style.opacity = "0";
        el.style.transform = "translateY(10px)";
        setTimeout(() => el.remove(), 250);
    }, 3500);
}

function showNotice(id, message, type="error") {
    const el = $(id);
    el.textContent = message;
    el.className = "notice " + type;
}

function openModal(id) {
    closeModals();
    $(id).classList.add("active");
}

function closeModals() {
    document.querySelectorAll(".modal-overlay").forEach(el => {
        if (el.id !== "confirmModal") {
            el.classList.remove("active");
        }
    });
}

function customConfirm(title, message, yesText="بله، انجام بده") {
    $("confirmTitle").textContent = title;
    $("confirmText").textContent = message;
    $("confirmYes").textContent = yesText;
    $("confirmModal").classList.add("active");

    return new Promise(resolve => {
        confirmResolver = resolve;
    });
}

function resolveConfirm(result) {
    $("confirmModal").classList.remove("active");

    if (confirmResolver) {
        confirmResolver(result);
        confirmResolver = null;
    }
}

function statusLabel(status) {
    const labels = {
        open:"باز",
        pending:"در انتظار بررسی",
        accepted:"تأیید شده",
        rejected:"رد شده",
        cancelled:"لغو شده",
        in_progress:"در حال انجام",
        closed:"تکمیل‌شده و بسته",
        none:"اعلام نشده",
        completion_pending:"در انتظار تأیید فریلنسر",
        completion_rejected:"درخواست پایان رد شده"
    };

    return labels[status] || status;
}

function statusBadge(status) {
    return `<span class="status status-${escapeHtml(status)}">${escapeHtml(statusLabel(status))}</span>`;
}

function formatDate(value) {
    if (!value) return "ثبت نشده";
    return escapeHtml(value);
}

function currentJalaliParts() {
    try {
        const parts = new Intl.DateTimeFormat("en-US-u-ca-persian", {
            year:"numeric",month:"numeric",day:"numeric"
        }).formatToParts(new Date());
        const get = type => Number(parts.find(part => part.type === type)?.value);
        const result = {year:get("year"),month:get("month"),day:get("day")};
        if (result.year > 1200 && result.month >= 1 && result.month <= 12 && result.day >= 1) return result;
    } catch {}
    return {year:1405,month:1,day:1};
}
function currentJalaliYear() { return currentJalaliParts().year; }

const jalaliMonthNames = ["فروردین","اردیبهشت","خرداد","تیر","مرداد","شهریور","مهر","آبان","آذر","دی","بهمن","اسفند"];
let jalaliSelection = {day:1, month:1, year:currentJalaliYear()};
let wheelScrollTimers = {};

function jalaliLeapYear(year) {
    const breaks = [-61, 9, 38, 199, 426, 686, 756, 818, 1111, 1181, 1210, 1635, 2060, 2097, 2192, 2262, 2324, 2394, 2456, 3178];
    let jp = breaks[0], jump = 0, jm = 0, leap = 0;
    if (year < jp || year >= breaks[breaks.length - 1]) return false;
    for (let i=1;i<breaks.length;i++) {
        jm = breaks[i]; jump = jm - jp;
        if (year < jm) break;
        jp = jm;
    }
    let n = year - jp;
    if (jump - n < 6) n = n - jump + Math.floor((jump + 4) / 33) * 33;
    leap = (((n + 1) % 33) - 1) % 4;
    if (leap === -1) leap = 4;
    return leap === 0;
}

function jalaliMonthDays(month, year) {
    if (month <= 6) return 31;
    if (month <= 11) return 30;
    return jalaliLeapYear(year) ? 30 : 29;
}

function faDigits(value) { return String(value).replace(/\d/g, d => "۰۱۲۳۴۵۶۷۸۹"[Number(d)]); }

function wheelItems(type) {
    if (type === "month") return jalaliMonthNames.map((name,index)=>({value:index+1,label:name}));
    if (type === "year") {
        const min = currentJalaliYear();
        return Array.from({length:31},(_,i)=>({value:min+i,label:faDigits(min+i)}));
    }
    return Array.from({length:jalaliMonthDays(jalaliSelection.month,jalaliSelection.year)},(_,i)=>({value:i+1,label:faDigits(i+1)}));
}

function renderWheel(type, preserve=true) {
    const wheel = $("jalali" + type[0].toUpperCase() + type.slice(1) + "Wheel");
    if (!wheel) return;
    const previous = jalaliSelection[type];
    const items = wheelItems(type);
    wheel.innerHTML = items.map(item => `
        <div class="jalali-wheel-item ${item.value === previous ? "selected" : ""}"
             data-value="${item.value}" onclick="chooseWheelValue('${type}',${item.value})">${item.label}</div>
    `).join("");
    if (!items.some(item=>item.value===previous)) jalaliSelection[type]=items[items.length-1].value;
    const selected = wheel.querySelector(`[data-value="${jalaliSelection[type]}"]`);
    if (selected && preserve) wheel.scrollTop = selected.offsetTop - 84;
    else if (selected) wheel.scrollTop = selected.offsetTop - 84;
}

function chooseWheelValue(type, value) {
    if (type === "year" && value < currentJalaliYear()) return;
    jalaliSelection[type] = value;
    if (type === "month" || type === "year") {
        const maxDay = jalaliMonthDays(jalaliSelection.month,jalaliSelection.year);
        if (jalaliSelection.day > maxDay) jalaliSelection.day = maxDay;
        renderWheel("day", true);
    }
    renderWheel(type, true);
    updateJalaliPreview();
}

function updateJalaliPreview() {
    const {day,month,year}=jalaliSelection;
    const value = `${day} / ${month} / ${year}`;
    $("projectDeadline").value = value;
    $("jalaliDatePreview").textContent = `${faDigits(day)} ${jalaliMonthNames[month-1]} ${faDigits(year)}`;
    ["month","day","year"].forEach(type=>{
        const wheel=$("jalali"+type[0].toUpperCase()+type.slice(1)+"Wheel");
        if (!wheel) return;
        wheel.querySelectorAll(".jalali-wheel-item").forEach(item=>item.classList.toggle("selected",Number(item.dataset.value)===jalaliSelection[type]));
    });
}

function initJalaliPicker() {
    const nowYear=currentJalaliYear();
    const today=currentJalaliParts();
    jalaliSelection={day:today.day,month:today.month,year:Math.max(today.year,nowYear)};
    renderWheel("month",false); renderWheel("day",false); renderWheel("year",false);
    ["month","day","year"].forEach(type=>{
        const wheel=$("jalali"+type[0].toUpperCase()+type.slice(1)+"Wheel");
        wheel.addEventListener("scroll",()=>{
            clearTimeout(wheelScrollTimers[type]);
            wheelScrollTimers[type]=setTimeout(()=>{
                const index=Math.max(0,Math.min(wheelItems(type).length-1,Math.round(wheel.scrollTop/42)));
                const item=wheelItems(type)[index];
                if (item) chooseWheelValue(type,item.value);
            },90);
        },{passive:true});
    });
    updateJalaliPreview();
}


/* ==========================================================
   NAVIGATION
========================================================== */

function updateNavigation() {
    if (currentUser) {
        $("guestActions").classList.add("hidden");
        $("userActions").classList.remove("hidden");
        $("navAvatar").textContent = currentUser.name.charAt(0);
        $("sideAvatar").textContent = currentUser.name.charAt(0);
        $("sideName").textContent = currentUser.name;
        $("sideEmail").textContent = currentUser.email;
    } else {
        $("guestActions").classList.remove("hidden");
        $("userActions").classList.add("hidden");
    }
}

function navigateToSection(sectionId) {
    // اگر کاربر داخل پنل کاربری باشد، ابتدا صفحه اصلی را دوباره نمایش بده.
    $("dashboard").style.display = "none";
    $("home").style.display = "block";
    stopChatPolling();

    const target = $(sectionId);
    if (!target) return;

    // پس از قابل‌مشاهده شدن صفحه اصلی، به بخش انتخاب‌شده برو.
    requestAnimationFrame(() => {
        if (sectionId === "home") {
            window.scrollTo({top: 0, behavior: "smooth"});
        } else {
            target.scrollIntoView({behavior: "smooth", block: "start"});
        }
    });
}

function showHome() {
    $("dashboard").style.display = "none";
    $("home").style.display = "block";
    stopChatPolling();
    window.scrollTo({top:0,behavior:"smooth"});
}

function scrollToProjects() {
    $("projects").scrollIntoView({behavior:"smooth"});
}

function filterCategory(category) {
    $("categoryFilter").value = category;
    scrollToProjects();
    renderProjects();
}

function openProjectModal() {
    if (!currentUser) {
        toast("برای ثبت پروژه ابتدا وارد حساب خودت شو.", "error");
        openModal("loginModal");
        return;
    }
    initJalaliPicker();
    openModal("projectModal");
}


/* ==========================================================
   AUTHENTICATION
========================================================== */

async function register(event) {
    event.preventDefault();

    try {
        const result = await api("/api/register", "POST", {
            name:$("registerName").value,
            email:$("registerEmail").value,
            password:$("registerPassword").value,
            role:$("registerRole").value
        });

        currentUser = result.user;
        updateNavigation();
        closeModals();
        event.target.reset();
        await loadAll();
        toast("حساب کاربری با موفقیت ساخته شد.");
    } catch (error) {
        showNotice("registerNotice", error.message);
    }
}

async function login(event) {
    event.preventDefault();

    try {
        const result = await api("/api/login", "POST", {
            email:$("loginEmail").value,
            password:$("loginPassword").value
        });

        currentUser = result.user;
        updateNavigation();
        closeModals();
        event.target.reset();
        await loadAll();
        toast("خوش آمدی!");
    } catch (error) {
        showNotice("loginNotice", error.message);
    }
}

async function logout() {
    try {
        await api("/api/logout", "POST", {});
    } catch {}

    currentUser = null;
    dashboardData = null;
    activeProposalId = null;
    stopChatPolling();
    updateNavigation();
    showHome();
    await loadAll();
    toast("از حساب کاربری خارج شدی.");
}


/* ==========================================================
   PROJECTS
========================================================== */

function renderProjects() {
    const search = $("projectSearch").value.trim().toLowerCase();
    const category = $("categoryFilter").value;

    const filtered = projects.filter(project => {
        const text = (
            project.title + " " +
            project.description + " " +
            project.owner_name
        ).toLowerCase();

        return text.includes(search) &&
            (!category || project.category === category);
    });

    if (!filtered.length) {
        $("projectList").innerHTML =
            '<div class="empty">پروژه‌ای مطابق جستجوی شما پیدا نشد.</div>';
        return;
    }

    $("projectList").innerHTML = filtered.map(project => {
        const own = currentUser && currentUser.id === project.owner_id;

        return `
        <article class="project-card">
            <span class="tag">${escapeHtml(project.category)}</span>
            ${statusBadge(project.status)}
            <div class="owner-line">
                <span>کارفرما:</span>
                <button class="profile-link" onclick="openUserProfile(${project.owner_id})">${escapeHtml(project.owner_name)}</button>
                ${project.owner_verified ? '<span class="gold-verified" title="نشان طلایی">✓</span>' : ''}
                ${project.owner_rating_count ? `<span class="rating-summary">★ ${Number(project.owner_rating).toFixed(1)}</span>` : ''}
                <span style="margin-right:auto">${escapeHtml(project.created_at)}</span>
            </div>

            <h3>${escapeHtml(project.title)}</h3>
            <p>${escapeHtml(project.description)}</p>

            <div class="project-meta">
                <span class="project-price">${numberFa(project.budget)} تومان</span>
                <span>مهلت: ${formatDate(project.deadline || "توافقی")}</span>
            </div>

            ${
                own
                ? `<button class="btn btn-light" onclick="showDashboardAndTab('myprojects')">مدیریت پروژه</button>`
                : project.status === "open"
                    ? `<button class="btn btn-primary" onclick="openProposal(${project.id})">ارسال پیشنهاد</button>`
                    : `<button class="btn btn-outline" disabled>پروژه در حال انجام</button>`
            }
        </article>
        `;
    }).join("");
}

async function createProject(event) {
    event.preventDefault();

    try {
        const result = await api("/api/projects", "POST", {
            title:$("projectTitle").value,
            category:$("projectCategory").value,
            description:$("projectDescription").value,
            budget:$("projectBudget").value,
            deadline:$("projectDeadline").value
        });

        closeModals();
        event.target.reset();
        await loadAll();
        toast(result.message || "پروژه با موفقیت ثبت شد.");
    } catch (error) {
        showNotice("projectNotice", error.message);
    }
}

async function deleteProject(projectId) {
    const confirmed = await customConfirm(
        "حذف پروژه",
        "آیا مطمئنی می‌خواهی این پروژه را حذف کنی؟ تمام پیشنهادهای مرتبط با این پروژه نیز حذف می‌شوند.",
        "حذف پروژه"
    );

    if (!confirmed) return;

    try {
        await api(`/api/projects/${projectId}/delete`, "POST", {});
        await loadAll();
        await loadDashboard();
        toast("پروژه و پیشنهادهای مرتبط حذف شدند.");
    } catch (error) {
        toast(error.message, "error");
    }
}


/* ==========================================================
   PROPOSALS
========================================================== */

function openProposal(projectId) {
    if (!currentUser) {
        toast("برای ارسال پیشنهاد ابتدا وارد حساب کاربری شو.", "error");
        openModal("loginModal");
        return;
    }

    $("proposalProjectId").value = projectId;
    openModal("proposalModal");
}

async function sendProposal(event) {
    event.preventDefault();

    try {
        await api("/api/proposals", "POST", {
            project_id:$("proposalProjectId").value,
            amount:$("proposalAmount").value,
            days:$("proposalDays").value,
            message:$("proposalMessage").value
        });

        closeModals();
        event.target.reset();
        await loadAll();
        await loadDashboard();
        toast("پیشنهادت با موفقیت برای کارفرما ارسال شد.");
    } catch (error) {
        showNotice("proposalNotice", error.message);
    }
}

async function decideProposal(proposalId, decision) {
    const isAccept = decision === "accept";

    const confirmed = await customConfirm(
        isAccept ? "تأیید پیشنهاد" : "رد پیشنهاد",
        isAccept
            ? "با تأیید این پیشنهاد، فریلنسر انتخاب می‌شود و گفتگوی خصوصی بین شما فعال خواهد شد. پیشنهادهای دیگر این پروژه رد خواهند شد."
            : "آیا از رد کردن این پیشنهاد مطمئنی؟ بعد از رد شدن، این پیشنهاد دیگر قابل تأیید نیست.",
        isAccept ? "تأیید و شروع گفتگو" : "رد پیشنهاد"
    );

    if (!confirmed) return;

    try {
        const result = await api(
            `/api/proposals/${proposalId}/decision`,
            "POST",
            {decision}
        );

        await loadAll();
        await loadDashboard();

        if (isAccept) {
            toast("پیشنهاد تأیید شد. گفتگوی خصوصی فعال شد.");
            switchTab("chats");
            await loadChats();
        } else {
            toast(result.message || "پیشنهاد رد شد.");
        }
    } catch (error) {
        toast(error.message, "error");
    }
}

async function cancelProposal(proposalId) {
    const confirmed = await customConfirm(
        "لغو پیشنهاد",
        "آیا می‌خواهی پیشنهاد ارسال‌شده خودت را لغو کنی؟",
        "لغو پیشنهاد"
    );

    if (!confirmed) return;

    try {
        await api(`/api/proposals/${proposalId}/cancel`, "POST", {});
        await loadAll();
        await loadDashboard();
        toast("پیشنهاد لغو شد.");
    } catch (error) {
        toast(error.message, "error");
    }
}



async function requestProjectCompletion(projectId) {
    const confirmed = await customConfirm(
        "اعلام پایان پروژه",
        "با این کار درخواست تأیید پایان برای فریلنسر ارسال می‌شود. پروژه فقط پس از تأیید فریلنسر بسته خواهد شد.",
        "ارسال درخواست پایان"
    );
    if (!confirmed) return;
    try {
        const result=await api(`/api/projects/${projectId}/completion/request`,"POST",{});
        await loadAll(); await loadDashboard();
        toast(result.message || "درخواست پایان برای فریلنسر ارسال شد.");
    } catch(error) { toast(error.message,"error"); }
}

async function decideProjectCompletion(proposalId, decision) {
    const accept=decision==="confirm";
    const confirmed=await customConfirm(
        accept ? "تأیید پایان پروژه" : "رد درخواست پایان",
        accept
            ? "آیا تأیید می‌کنی که پروژه کامل تحویل شده است؟ با تأیید تو، پروژه به‌صورت نهایی بسته می‌شود."
            : "با رد درخواست، پروژه باز و در وضعیت انجام باقی می‌ماند و کارفرما می‌تواند بعداً دوباره درخواست بدهد.",
        accept ? "بله، پروژه تمام شده" : "رد درخواست"
    );
    if (!confirmed) return;
    try {
        const result=await api(`/api/proposals/${proposalId}/completion/decision`,"POST",{decision});
        await loadAll(); await loadDashboard();
        toast(result.message || "وضعیت پایان پروژه ثبت شد.");
    } catch(error) { toast(error.message,"error"); }
}

function completionPanel(project, side, proposalId=null) {
    const state=project.completion_status || "none";
    if (state==="completed" || project.status==="closed") {
        return `<div class="completion-box done">✓ پروژه با تأیید هر دو طرف تکمیل و بسته شده است.</div>`;
    }
    if (state==="pending") {
        if (side==="freelancer") {
            return `<div class="completion-box pending">کارفرما پایان پروژه را اعلام کرده است. لطفاً تحویل کامل پروژه را بررسی و نتیجه را ثبت کن.
                <div class="completion-actions">
                    <button class="btn btn-green btn-sm" onclick="decideProjectCompletion(${proposalId},'confirm')">✓ تأیید پایان پروژه</button>
                    <button class="btn btn-red btn-sm" onclick="decideProjectCompletion(${proposalId},'reject')">✕ پروژه هنوز تمام نشده</button>
                </div></div>`;
        }
        return `<div class="completion-box pending">درخواست پایان پروژه ارسال شده و منتظر تأیید فریلنسر است.</div>`;
    }
    if (state==="rejected") {
        if (side==="employer") return `<div class="completion-box rejected">فریلنسر درخواست پایان قبلی را تأیید نکرده است. پس از تکمیل موارد باقی‌مانده می‌توانی دوباره درخواست بدهی.
            <div class="completion-actions"><button class="btn btn-primary btn-sm" onclick="requestProjectCompletion(${project.id})">ارسال مجدد درخواست پایان</button></div></div>`;
        return `<div class="completion-box rejected">درخواست قبلی پایان پروژه رد شده است؛ پروژه همچنان در حال انجام است.</div>`;
    }
    if (side==="employer" && project.status==="in_progress") {
        return `<div class="completion-box">اگر کار تحویل شده و از اتمام آن مطمئنی، درخواست رسمی پایان را برای فریلنسر ارسال کن.
            <div class="completion-actions"><button class="btn btn-green btn-sm" onclick="requestProjectCompletion(${project.id})">✓ اعلام پایان پروژه</button></div></div>`;
    }
    return "";
}

/* ==========================================================
   DASHBOARD
========================================================== */

function showDashboardAndTab(tab) {
    showDashboard();
    switchTab(tab);
}

async function showDashboard() {
    if (!currentUser) {
        openModal("loginModal");
        return;
    }

    $("home").style.display = "none";
    $("dashboard").style.display = "block";
    window.scrollTo({top:0,behavior:"smooth"});

    await loadDashboard();
    switchTab(currentTab || "overview");
}

function switchTab(tab, button=null) {
    currentTab = tab;

    document.querySelectorAll(".tab-content").forEach(el => {
        el.classList.add("hidden");
    });

    const target = $("tab-" + tab);
    if (target) target.classList.remove("hidden");

    document.querySelectorAll(".side-link").forEach(el => {
        el.classList.toggle("active", el.dataset.tab === tab);
    });

    const titles = {
        overview:["نمای کلی","خلاصه فعالیت‌های حساب کاربری شما"],
        myprojects:["پروژه‌های من","مدیریت، بررسی و حذف پروژه‌های ثبت‌شده"],
        incoming:["پیشنهادهای دریافتی","بررسی پیشنهادهای فریلنسرها"],
        outgoing:["پیشنهادهای ارسالی","پیگیری وضعیت پیشنهادهای ارسال‌شده"],
        chats:["پیام‌ها و گفتگوها","ارتباط مستقیم با طرف قرارداد"],
        profile:["پروفایل من","اطلاعات و مشخصات حساب کاربری"]
    };

    const info = titles[tab] || titles.overview;
    $("dashHeading").textContent = info[0];
    $("dashDescription").textContent = info[1];

    if (tab === "chats") {
        loadChats();
    } else {
        stopChatPolling();
    }

    if (tab === "profile") {
        fillProfile();
    }
}

async function loadDashboard() {
    if (!currentUser) return;

    try {
        dashboardData = await api("/api/dashboard");
        renderDashboard();
    } catch (error) {
        toast(error.message, "error");
    }
}

function renderDashboard() {
    if (!dashboardData) return;

    const data = dashboardData;

    $("myProjectCount").textContent = numberFa(data.projects.length);
    $("myIncomingCount").textContent = numberFa(data.incoming.length);
    $("myChatCount").textContent = numberFa(data.chats.length);

    renderMyProjects(data.projects);
    renderIncoming(data.incoming);
    renderOutgoing(data.outgoing);
    renderRecent(data);
}

function renderRecent(data) {
    const items = [];

    data.projects.slice(0,3).forEach(project => {
        items.push(`
            <div class="list-item">
                <h4>پروژه ثبت کردی: ${escapeHtml(project.title)}</h4>
                <p>وضعیت: ${escapeHtml(statusLabel(project.status))}</p>
            </div>
        `);
    });

    data.incoming.slice(0,3).forEach(proposal => {
        items.push(`
            <div class="list-item">
                <h4>پیشنهاد جدید از ${escapeHtml(proposal.freelancer_name)}</h4>
                <p>برای پروژه ${escapeHtml(proposal.project_title)}</p>
            </div>
        `);
    });

    $("recentActivity").innerHTML = items.length
        ? items.join("")
        : '<div class="empty">هنوز فعالیتی در حساب شما ثبت نشده است.</div>';
}

function renderMyProjects(items) {
    if (!items.length) {
        $("myProjectsList").innerHTML =
            '<div class="empty">هنوز پروژه‌ای ثبت نکرده‌ای.</div>';
        return;
    }

    $("myProjectsList").innerHTML = items.map(project => `
        <div class="list-item">
            <div class="list-top">
                <div>
                    <h4>${escapeHtml(project.title)}</h4>
                    <p>
                        ${escapeHtml(project.category)}
                        · بودجه ${numberFa(project.budget)} تومان
                        · مهلت ${formatDate(project.deadline || "توافقی")}
                    </p>
                </div>
                <div>${statusBadge(project.status)}</div>
            </div>

            ${completionPanel(project,"employer")}
            <div class="list-actions">
                <button class="btn btn-light btn-sm"
                    onclick="showDashboardAndTab('incoming')">
                    مشاهده پیشنهادها
                </button>
                ${
                    project.status !== "in_progress" && project.status !== "closed"
                    ? `<button class="btn btn-red btn-sm" onclick="deleteProject(${project.id})">حذف پروژه</button>`
                    : `<span class="small muted">${project.status === "closed" ? "پروژه نهایی بسته شده است." : "پروژه در حال انجام است؛ حذف آن غیرفعال است."}</span>`
                }
            </div>
        </div>
    `).join("");
}

function renderIncoming(items) {
    if (!items.length) {
        $("incomingList").innerHTML =
            '<div class="empty">هنوز پیشنهادی برای پروژه‌هایت دریافت نکرده‌ای.</div>';
        return;
    }

    $("incomingList").innerHTML = items.map(proposal => `
        <div class="proposal-card">
            <div class="list-top">
                <div>
                    <h4>${escapeHtml(proposal.project_title)}</h4>
                    <p>
                        فریلنسر: <strong>${escapeHtml(proposal.freelancer_name)}</strong>
                        · ${escapeHtml(proposal.freelancer_email)}
                    </p>
                </div>
                ${statusBadge(proposal.status)}
            </div>

            <p style="margin-top:12px">
                مبلغ پیشنهادی:
                <strong style="color:var(--green)">
                    ${numberFa(proposal.amount)} تومان
                </strong>
                · زمان انجام: ${numberFa(proposal.days)} روز
            </p>

            <p style="margin-top:8px">${escapeHtml(proposal.message)}</p>
            ${proposal.status === "accepted" ? completionPanel(proposal,"employer") : ""}

            <div class="proposal-actions">
                ${
                    proposal.status === "pending"
                    ? `
                        <button class="btn btn-green btn-sm"
                            onclick="decideProposal(${proposal.id},'accept')">
                            ✓ تأیید و شروع گفتگو
                        </button>
                        <button class="btn btn-red btn-sm"
                            onclick="decideProposal(${proposal.id},'reject')">
                            ✕ رد پیشنهاد
                        </button>
                    `
                    : proposal.status === "accepted"
                        ? `
                            <button class="btn btn-primary btn-sm"
                                onclick="openChatFromProposal(${proposal.id})">
                                ✉ ورود به گفتگو
                            </button>
                        `
                        : `<span class="small muted">این پیشنهاد ${escapeHtml(statusLabel(proposal.status))} است.</span>`
                }
            </div>
        </div>
    `).join("");
}

function renderOutgoing(items) {
    if (!items.length) {
        $("outgoingList").innerHTML =
            '<div class="empty">هنوز برای هیچ پروژه‌ای پیشنهاد ارسال نکرده‌ای.</div>';
        return;
    }

    $("outgoingList").innerHTML = items.map(proposal => `
        <div class="proposal-card">
            <div class="list-top">
                <div>
                    <h4>${escapeHtml(proposal.project_title)}</h4>
                    <p>کارفرما: ${escapeHtml(proposal.employer_name)}</p>
                </div>
                ${statusBadge(proposal.status)}
            </div>

            <p style="margin-top:10px">
                مبلغ: <strong>${numberFa(proposal.amount)} تومان</strong>
                · زمان: ${numberFa(proposal.days)} روز
            </p>

            <p>${escapeHtml(proposal.message)}</p>
            ${proposal.status === "accepted" ? completionPanel(proposal,"freelancer",proposal.id) : ""}

            <div class="proposal-actions">
                ${
                    proposal.status === "pending"
                    ? `<button class="btn btn-red btn-sm" onclick="cancelProposal(${proposal.id})">لغو پیشنهاد</button>`
                    : proposal.status === "accepted"
                        ? `<button class="btn btn-primary btn-sm" onclick="openChatFromProposal(${proposal.id})">ورود به گفتگو</button>`
                        : `<span class="small muted">وضعیت: ${escapeHtml(statusLabel(proposal.status))}</span>`
                }
            </div>
        </div>
    `).join("");
}


/* ==========================================================
   PROFILE
========================================================== */

function fillProfile() {
    if (!currentUser) return;

    $("profileName").value = currentUser.name || "";
    $("profileRole").value = currentUser.role || "both";
    $("profileBio").value = currentUser.bio || "";
    $("profileSkills").value = currentUser.skills || "";
}

async function saveProfile(event) {
    event.preventDefault();

    try {
        const result = await api("/api/profile", "POST", {
            name:$("profileName").value,
            role:$("profileRole").value,
            bio:$("profileBio").value,
            skills:$("profileSkills").value
        });

        currentUser = result.user;
        updateNavigation();
        await loadDashboard();
        toast("پروفایل با موفقیت به‌روزرسانی شد.");
    } catch (error) {
        toast(error.message, "error");
    }
}


/* ==========================================================
   PUBLIC PROFILES & 10-STAR RATINGS
========================================================== */

function verifiedBadge(isVerified) {
    return isVerified ? '<span class="gold-verified" title="نشان طلایی Freelance Tehran">✓</span>' : '';
}

function ratingText(score, count) {
    if (!count) return '';
    return `<span class="rating-summary">★ ${Number(score).toFixed(1)}</span>`;
}

function renderStars(score, interactive=false) {
    const value = Math.max(0, Math.min(10, Number(score || 0)));
    let html = '<div class="rating-stars' + (interactive ? ' interactive' : '') + '">';
    for (let i=1;i<=10;i++) {
        const state = value >= i ? 'full' : (value >= i-0.5 ? 'half' : '');
        html += interactive
            ? `<button type="button" class="rating-star ${state}" aria-label="${i} ستاره" onclick="changeRatingStar(${i})">★</button>`
            : `<span class="rating-star ${state}" aria-hidden="true">★</span>`;
    }
    return html + '</div>';
}

function changeRatingStar(index) {
    const halfValue = index - 0.5;
    if (ratingDraftScore === halfValue) {
        ratingDraftScore = index;
    } else if (ratingDraftScore === index) {
        ratingDraftScore = index - 1;
    } else {
        ratingDraftScore = halfValue;
    }
    const stars = $('ratingInputStars');
    if (stars) stars.innerHTML = renderStars(ratingDraftScore, true);
    const value = $('ratingDraftValue');
    if (value) value.textContent = ratingDraftScore.toFixed(1) + ' از ۱۰';
}

async function openUserProfile(userId) {
    $('userProfileContent').innerHTML = '<div class="empty">در حال بارگذاری پروفایل...</div>';
    openModal('userProfileModal');
    activeRatingProfileId = Number(userId);
    ratingDraftScore = 0;

    try {
        const data = await api(`/api/users/${Number(userId)}`);
        activeRatingEligibleProjects = data.eligible_projects || [];
        const user = data.user;
        const isOwn = currentUser && Number(currentUser.id) === Number(user.id);
        const average = Number(data.average_rating || 0);
        const count = Number(data.rating_count || 0);
        const roleLabels = {freelancer:'فریلنسر',employer:'کارفرما',both:'فریلنسر و کارفرما'};
        const reviews = (data.ratings || []).map(item => `
            <div class="rating-review">
                <div class="rating-review-head">
                    <button class="profile-link" onclick="openUserProfile(${item.reviewer_id})">${escapeHtml(item.reviewer_name)}</button>
                    <span class="rating-summary">★ ${Number(item.score).toFixed(1)}</span>
                </div>
                <div class="small muted">${escapeHtml(item.created_at || '')} · ${escapeHtml(item.project_title || 'همکاری تکمیل‌شده')}</div>
                ${item.comment ? `<p>${escapeHtml(item.comment)}</p>` : ''}
            </div>
        `).join('');

        let rateBox = '';
        if (!isOwn && currentUser && activeRatingEligibleProjects.length) {
            const options = activeRatingEligibleProjects.map(project =>
                `<option value="${project.proposal_id}">${escapeHtml(project.project_title)}</option>`
            ).join('');
            rateBox = `
                <div class="rating-form-box">
                    <h3 class="profile-section-title" style="margin-top:0">ثبت امتیاز برای این کاربر</h3>
                    <label class="small">پروژه‌ای که با هم انجام داده‌اید</label>
                    <select class="field" id="ratingProjectId">${options}</select>
                    <div style="display:flex;align-items:center;gap:12px;flex-wrap:wrap;margin-top:13px">
                        <div id="ratingInputStars">${renderStars(0,true)}</div>
                        <strong id="ratingDraftValue" style="color:#b77c10">0.0 از ۱۰</strong>
                    </div>
                    <div class="rating-help">روی هر ستاره بار اول نیم‌ستاره، بار دوم ستاره کامل و بار سوم خالی می‌شود. امتیاز از ۰٫۵ تا ۱۰ قابل انتخاب است.</div>
                    <textarea class="field" id="ratingComment" rows="3" maxlength="500" style="margin-top:12px" placeholder="نظر شما درباره تجربه همکاری (اختیاری)"></textarea>
                    <button class="btn btn-primary" style="margin-top:10px" onclick="submitUserRating()">ثبت امتیاز</button>
                </div>`;
        } else if (!isOwn && currentUser) {
            rateBox = '<div class="rating-help" style="margin-top:14px">برای امتیاز دادن باید یک پروژه مشترک با این کاربر به‌صورت کامل بسته شده باشد و هنوز برای آن همکاری امتیاز ثبت نکرده باشی.</div>';
        } else if (!isOwn && !currentUser) {
            rateBox = '<div class="rating-help" style="margin-top:14px">برای امتیاز دادن ابتدا وارد حساب کاربری شو.</div>';
        }

        $('userProfileContent').innerHTML = `
            <div class="profile-hero">
                <div class="profile-avatar">${escapeHtml((user.name || '?').charAt(0))}</div>
                <div style="min-width:0;flex:1">
                    <h3>${escapeHtml(user.name)} ${verifiedBadge(data.is_verified)}</h3>
                    <div class="small muted">${escapeHtml(roleLabels[user.role] || 'کاربر')} · عضویت از ${escapeHtml((user.created_at || '').slice(0,10))}</div>
                    <div style="display:flex;align-items:center;gap:12px;flex-wrap:wrap;margin-top:7px">
                        <span class="profile-rating-number">${count ? average.toFixed(1) : '—'}</span>
                        ${ratingText(average,count)}
                    </div>
                </div>
            </div>
            <h3 class="profile-section-title">درباره من</h3>
            <p style="font-size:13px;line-height:2.1;white-space:pre-wrap">${escapeHtml(user.bio || 'هنوز توضیحی ثبت نشده است.')}</p>
            <h3 class="profile-section-title">مهارت‌ها</h3>
            <p style="font-size:13px;line-height:2">${escapeHtml(user.skills || 'هنوز مهارتی ثبت نشده است.')}</p>
            ${rateBox}
            <h3 class="profile-section-title">نظرات و امتیازها (${numberFa(count)})</h3>
            ${reviews || '<div class="rating-empty">هنوز نظری برای این کاربر ثبت نشده است.</div>'}
        `;
    } catch (error) {
        $('userProfileContent').innerHTML = `<div class="empty">${escapeHtml(error.message)}</div>`;
    }
}

async function submitUserRating() {
    if (!currentUser) {
        toast('برای ثبت امتیاز ابتدا وارد حساب کاربری شو.','error');
        return;
    }
    if (!activeRatingProfileId || !ratingDraftScore || ratingDraftScore < 0.5) {
        toast('ابتدا امتیاز خودت را با ستاره‌ها انتخاب کن.','error');
        return;
    }
    const proposalId = Number($('ratingProjectId')?.value || 0);
    try {
        await api('/api/ratings','POST',{
            reviewee_id:activeRatingProfileId,
            proposal_id:proposalId,
            score:ratingDraftScore,
            comment:$('ratingComment')?.value || ''
        });
        toast('امتیاز شما با موفقیت ثبت شد.');
        await loadAll();
        await openUserProfile(activeRatingProfileId);
    } catch (error) {
        toast(error.message,'error');
    }
}


/* ==========================================================
   CHAT SYSTEM
========================================================== */

function stopChatPolling() {
    if (activeChatTimer) {
        clearInterval(activeChatTimer);
        activeChatTimer = null;
    }
}

async function loadChats() {
    if (!currentUser) return;

    try {
        const result = await api("/api/chats");

        if (!result.chats.length) {
            $("chatContacts").innerHTML =
                '<div class="empty">هنوز گفتگوی فعالی نداری. گفتگو پس از تأیید پیشنهاد ایجاد می‌شود.</div>';
            return;
        }

        $("chatContacts").innerHTML = result.chats.map(chat => `
            <button class="chat-contact ${activeProposalId === chat.proposal_id ? "active" : ""}"
                onclick="selectChat(${chat.proposal_id})">
                <strong>${escapeHtml(chat.other_name)}</strong>
                <span>${escapeHtml(chat.project_title)}</span>
                <span>${numberFa(chat.amount)} تومان</span>
            </button>
        `).join("");

        if (activeProposalId) {
            const stillExists = result.chats.some(
                chat => chat.proposal_id === activeProposalId
            );

            if (!stillExists) {
                activeProposalId = null;
                $("chatActive").classList.add("hidden");
                $("chatPlaceholder").classList.remove("hidden");
            }
        }
    } catch (error) {
        toast(error.message, "error");
    }
}

async function openChatFromProposal(proposalId) {
    showDashboard();
    switchTab("chats");
    await loadChats();
    await selectChat(proposalId);
}

async function selectChat(proposalId) {
    activeProposalId = proposalId;
    chatLastMessageId = 0;

    $("chatPlaceholder").classList.add("hidden");
    $("chatActive").classList.remove("hidden");
    $("chatMessages").innerHTML = '<div class="chat-placeholder">در حال بارگذاری گفتگو...</div>';

    await loadChatMessages(true);
    await loadChats();

    stopChatPolling();
    activeChatTimer = setInterval(() => {
        loadChatMessages(false);
    }, 2200);
}

async function loadChatMessages(reset=false) {
    if (!activeProposalId) return;

    try {
        const result = await api(
            `/api/chats/${activeProposalId}?after=${chatLastMessageId}`
        );

        $("chatTitle").textContent =
            result.project_title + " — " + result.other_name;
        $("chatCompose").classList.toggle("hidden", !!result.project_closed);
        $("chatClosedNote").classList.toggle("hidden", !result.project_closed);

        if (reset) {
            $("chatMessages").innerHTML = "";
            chatLastMessageId = 0;
        }

        if (result.messages.length) {
            const box = $("chatMessages");

            result.messages.forEach(message => {
                if (message.id <= chatLastMessageId) return;

                const item = document.createElement("div");
                item.className = "message " +
                    (message.sender_id === currentUser.id ? "mine" : "theirs");

                const body = document.createElement("div");
                body.textContent = message.body;

                const time = document.createElement("time");
                time.textContent = message.created_at;

                item.appendChild(body);
                item.appendChild(time);
                box.appendChild(item);

                chatLastMessageId = Math.max(chatLastMessageId, message.id);
            });

            box.scrollTop = box.scrollHeight;
        }
    } catch (error) {
        stopChatPolling();
        toast(error.message, "error");
    }
}

async function sendChatMessage(event) {
    event.preventDefault();

    const body = $("chatInput").value.trim();

    if (!body || !activeProposalId) return;

    try {
        await api(`/api/chats/${activeProposalId}/send`, "POST", {body});
        $("chatInput").value = "";
        await loadChatMessages(false);
    } catch (error) {
        toast(error.message, "error");
    }
}


/* ==========================================================
   INITIAL LOAD
========================================================== */

async function loadAll() {
    try {
        const data = await api("/api/bootstrap");

        currentUser = data.user;
        projects = data.projects;

        updateNavigation();
        renderProjects();

        $("statProjects").textContent = numberFa(data.stats.projects);
        $("statUsers").textContent = numberFa(data.stats.users);
        $("statProposals").textContent = numberFa(data.stats.proposals);

        if (currentUser && $("dashboard").style.display === "block") {
            await loadDashboard();
        }
    } catch (error) {
        console.error(error);
    }
}

document.addEventListener("DOMContentLoaded", async () => {
    initJalaliPicker();
    await loadAll();

    document.querySelectorAll(".modal-overlay").forEach(modal => {
        modal.addEventListener("click", event => {
            if (event.target === modal && modal.id !== "confirmModal") {
                closeModals();
            }
        });
    });

    $("confirmModal").addEventListener("click", event => {
        if (event.target === $("confirmModal")) {
            resolveConfirm(false);
        }
    });
});
