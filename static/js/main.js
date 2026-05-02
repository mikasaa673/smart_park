/**
 * main.js
 * -------
 * Frontend logic for the Smart Parking index page.
 * Handles slot loading, reservation, predictions, and toasts.
 */

// ─── Configuration ───────────────────────────────────────────────────────
const API_BASE = '';  // same origin

// ─── DOM References ──────────────────────────────────────────────────────
const $parkingGrid = document.getElementById('parkingGrid');
const $slotSelect = document.getElementById('slotSelect');
const $totalCount = document.getElementById('totalCount');
const $availableCount = document.getElementById('availableCount');
const $occupiedCount = document.getElementById('occupiedCount');
const $formMessage = document.getElementById('formMessage');
const $predCards = document.getElementById('predictionCards');
const $toastContainer = document.getElementById('toastContainer');

// ─── Load Slots ──────────────────────────────────────────────────────────
async function loadSlots() {
    try {
        const res = await fetch(`${API_BASE}/slots`);
        const data = await res.json();
        if (!data.success) throw new Error(data.error);

        renderStats(data);
        renderGrid(data.slots);
        populateSlotSelect(data.slots);
    } catch (err) {
        console.error('loadSlots:', err);
        showToast('Failed to load slots.', 'error');
    }
}

function renderStats(data) {
    animateNumber($totalCount, data.total_slots);
    animateNumber($availableCount, data.available);
    animateNumber($occupiedCount, data.occupied);
}

function renderGrid(slots) {
    $parkingGrid.innerHTML = '';
    slots.forEach((s, i) => {
        const status = s.reservation_status || (s.is_occupied ? 'occupied' : 'available');
        let cls, statusText, icon;
        if (status === 'grace') {
            cls = 'reserved';
            statusText = `Reserved · ${s.grace_minutes_left}m left`;
            icon = '';
        } else if (status === 'occupied') {
            cls = 'occupied';
            statusText = 'Occupied';
            icon = '';
        } else {
            cls = 'available';
            statusText = 'Available';
            icon = '';
        }
        const label = s.slot_label || `Slot ${s.slot_id}`;
        const card = document.createElement('div');
        card.className = `slot-card ${cls}`;
        card.id = `slot-${s.slot_id}`;
        card.style.animationDelay = `${i * 40}ms`;
        card.style.animation = 'fadeSlideIn .4s ease backwards';
        card.innerHTML = `
            <div class="slot-label">${label}</div>
            <span class="slot-status">${statusText}</span>
            ${status === 'grace' ? `<span class="slot-reserved-by">${s.reserved_by}</span>` : ''}
            <span class="slot-car-icon">${icon}</span>
        `;
        if (cls === 'available') {
            card.style.cursor = 'pointer';
            card.addEventListener('click', () => selectSlot(s.slot_id, label));
        }
        $parkingGrid.appendChild(card);
    });
}

function populateSlotSelect(slots) {
    const current = $slotSelect.value;
    $slotSelect.innerHTML = '<option value="">Select a slot</option>';
    slots.filter(s => !s.is_occupied).forEach(s => {
        const opt = document.createElement('option');
        opt.value = s.slot_id;
        opt.textContent = s.slot_label || `Slot ${s.slot_id}`;
        $slotSelect.appendChild(opt);
    });
    if (current) $slotSelect.value = current;
}

function selectSlot(id, label) {
    $slotSelect.value = id;
    document.getElementById('reserveSection').scrollIntoView({ behavior: 'smooth' });
    showToast(`Selected slot ${label}`, 'info');
}

// ─── Reserve ─────────────────────────────────────────────────────────────
async function handleReserve(event) {
    event.preventDefault();
    hideMessage();
    document.getElementById('pdfDownloadArea').style.display = 'none';

    const carPlate  = document.getElementById('carPlate').value.trim().toUpperCase();
    const userEmail = document.getElementById('userEmail').value.trim();

    const payload = {
        user_name:  document.getElementById('userName').value.trim(),
        car_plate:  carPlate,
        user_email: userEmail,
        slot_id:    parseInt($slotSelect.value),
        start_time: document.getElementById('startTime').value,
        end_time:   document.getElementById('endTime').value
    };

    if (!payload.user_name || !payload.car_plate || !payload.user_email ||
        !payload.slot_id   || !payload.start_time || !payload.end_time) {
        showMessage('Please fill in all fields.', 'error');
        return;
    }

    try {
        const res = await fetch(`${API_BASE}/reserve`, {
            method: 'POST',
            headers: { 'Content-Type': 'application/json' },
            body: JSON.stringify(payload)
        });
        const data = await res.json();

        if (data.success) {
            showMessage(data.message, 'success');
            showToast('Reservation confirmed! Check your email for the token.', 'success');
            // Show PDF download link
            const pdfArea = document.getElementById('pdfDownloadArea');
            const pdfLink = document.getElementById('pdfDownloadLink');
            pdfLink.href = `/reservation-pdf/${data.reservation_id}`;
            pdfArea.style.display = 'block';
            document.getElementById('reserveForm').reset();
            // Re-set times after reset
            setDefaultTimes();
            loadSlots();
        } else {
            showMessage(data.error || 'Reservation failed.', 'error');
            showToast(data.error || 'Booking failed', 'error');
        }
    } catch (err) {
        console.error('handleReserve:', err);
        showMessage('Network error.', 'error');
    }
}

// ─── Predictions ─────────────────────────────────────────────────────────
async function loadPredictions() {
    try {
        const res = await fetch(`${API_BASE}/predict?hours=6`);
        const data = await res.json();
        if (!data.success) return;

        renderPredictions(data.predictions, data.total_slots);
    } catch (err) {
        console.error('loadPredictions:', err);
    }
}

function renderPredictions(predictions, total) {
    $predCards.innerHTML = '';
    predictions.forEach((p, i) => {
        const pct = p.occupancy_pct;
        const color = pct > 80 ? 'var(--red)' : pct > 50 ? 'var(--orange)' : 'var(--green)';
        const card = document.createElement('div');
        card.className = 'pred-card';
        card.style.animation = `fadeSlideIn .4s ease ${i * 60}ms backwards`;
        card.innerHTML = `
            <div class="pred-time">${p.hour}</div>
            <div class="pred-value" style="color:${color}">${p.predicted_available}</div>
            <div class="pred-label">slots free</div>
            <div class="pred-bar">
                <div class="pred-bar-fill" style="width:${pct}%; background:${color}"></div>
            </div>
        `;
        $predCards.appendChild(card);
    });
}

// ─── Refresh ─────────────────────────────────────────────────────────────
async function refreshSlots() {
    const btn = document.getElementById('btnRefresh');
    btn.disabled = true;
    btn.querySelector('.btn-icon').style.animation = 'spin .6s linear';

    // Optionally trigger a detection scan first
    try { await fetch(`${API_BASE}/detect`, { method: 'POST' }); } catch (_) { }

    await loadSlots();
    await loadPredictions();

    setTimeout(() => {
        btn.disabled = false;
        btn.querySelector('.btn-icon').style.animation = '';
    }, 700);
    showToast('Data refreshed', 'info');
}

// ─── Utilities ───────────────────────────────────────────────────────────
function animateNumber(el, target) {
    const start = parseInt(el.textContent) || 0;
    const diff = target - start;
    const dur = 500;
    const t0 = performance.now();
    function step(now) {
        const progress = Math.min((now - t0) / dur, 1);
        el.textContent = Math.round(start + diff * easeOut(progress));
        if (progress < 1) requestAnimationFrame(step);
    }
    requestAnimationFrame(step);
}
function easeOut(t) { return 1 - Math.pow(1 - t, 3); }

function showMessage(text, type) {
    $formMessage.textContent = text;
    $formMessage.className = `form-message ${type}`;
}
function hideMessage() {
    $formMessage.className = 'form-message';
    $formMessage.textContent = '';
}

function showToast(message, type = 'info') {
    const icons = { success: '<i class="material-icons" style="font-size:18px">check_circle</i>', error: '<i class="material-icons" style="font-size:18px">error</i>', info: '<i class="material-icons" style="font-size:18px">info</i>' };
    const toast = document.createElement('div');
    toast.className = `toast ${type}`;
    toast.innerHTML = `<span>${icons[type] || ''}</span><span>${message}</span>`;
    $toastContainer.appendChild(toast);
    setTimeout(() => { toast.style.opacity = '0'; setTimeout(() => toast.remove(), 300); }, 3500);
}

// ─── Spin animation (inline) ────────────────────────────────────────────
const styleSheet = document.createElement('style');
styleSheet.textContent = `@keyframes spin { to { transform: rotate(360deg); } }`;
document.head.appendChild(styleSheet);

// ─── Set default times ──────────────────────────────────────────────────
function setDefaultTimes() {
    const now = new Date();
    const end = new Date(now.getTime() + 60 * 60000);
    const fmt = d => {
        const y = d.getFullYear();
        const m = String(d.getMonth() + 1).padStart(2, '0');
        const day = String(d.getDate()).padStart(2, '0');
        const h = String(d.getHours()).padStart(2, '0');
        const min = String(d.getMinutes()).padStart(2, '0');
        return `${y}-${m}-${day}T${h}:${min}`;
    };
    const startEl = document.getElementById('startTime');
    startEl.value = fmt(now);
    startEl.readOnly = true;
    document.getElementById('endTime').value = fmt(end);
}

// ─── Peak Hours Chart ────────────────────────────────────────────────────
let peakChart = null;

async function loadPeakHours() {
    try {
        const res = await fetch(`${API_BASE}/dashboard-data`);
        const data = await res.json();
        if (!data.success || !data.peak_hours) return;
        renderPeakChart(data.peak_hours);
    } catch (err) {
        console.error('loadPeakHours:', err);
    }
}

function renderPeakChart(peakHours) {
    const ctx = document.getElementById('peakHoursChart');
    if (!ctx) return;

    // Sort by hour for readability
    const sorted = [...peakHours].sort((a, b) => a.hour.localeCompare(b.hour));
    const labels = sorted.map(p => p.hour);
    const data = sorted.map(p => p.avg_occupied);
    const colors = data.map(d => d > 30 ? '#ef4444' : d > 18 ? '#f59e0b' : '#22c55e');

    if (peakChart) peakChart.destroy();
    peakChart = new Chart(ctx, {
        type: 'bar',
        data: {
            labels,
            datasets: [{
                label: 'Avg Vehicles Parked',
                data,
                backgroundColor: colors.map(c => c + '88'),
                borderColor: colors,
                borderWidth: 1,
                borderRadius: 6,
            }]
        },
        options: {
            responsive: true,
            maintainAspectRatio: false,
            plugins: {
                legend: { display: false },
                tooltip: {
                    callbacks: {
                        afterBody: (items) => {
                            const idx = items[0].dataIndex;
                            return `Occupancy: ${sorted[idx].occupancy_pct}%`;
                        }
                    }
                }
            },
            scales: {
                y: {
                    beginAtZero: true,
                    grid: { color: 'rgba(255,255,255,.04)' },
                    title: { display: true, text: 'Vehicles', color: '#94a3b8' }
                },
                x: {
                    grid: { display: false },
                    title: { display: true, text: 'Hour', color: '#94a3b8' }
                }
            }
        }
    });
}

// ─── Init ────────────────────────────────────────────────────────────────
document.addEventListener('DOMContentLoaded', async () => {
    // Auto-trigger detection on first load so slots show real occupancy
    try { await fetch(`${API_BASE}/detect`, { method: 'POST' }); } catch (_) { }
    loadSlots();
    loadPredictions();
    loadPeakHours();
    setDefaultTimes();

    // ── Auto-sync with video feed ─────────────────────────────────────────
    // Slot grid refreshes every 3 s (fast DB read — keeps UI near real-time).
    // Detection runs every 5 s (YOLOv8 inference — heavier, so slightly less frequent).
    setInterval(loadSlots, 3_000);
    async function runDetect() {
        try { await fetch(`${API_BASE}/detect`, { method: 'POST' }); } catch (_) { }
    }
    setInterval(runDetect, 5_000);
    // Refresh predictions every 5 minutes
    setInterval(loadPredictions, 300_000);
});
