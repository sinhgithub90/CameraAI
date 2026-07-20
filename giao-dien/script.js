// script.js
let ALL_CAMERAS = [];
let ALL_USERS = [];
let selectedUsername = null;
let editingCameraId = null;
let currentGridLimit = 6;
let selectedLiveCameraId = null;
let PLAYBACK_SEGMENTS = [];
let PLAYBACK_GAPS = [];
let DASHBOARD_REFRESH_TIMER = null;

// 1. Hàm gọi API lấy danh sách camera tập trung từ Backend FastAPI
async function fetchCamerasFromBackend() {
  try {
    const response = await fetch('http://127.0.0.1:8000/api/vms/cameras');
    if (response.ok) {
      ALL_CAMERAS = await response.json();
    }
  } catch (error) {
    console.error("❌ Không thể kết nối tới FastAPI (Port 8000)", error);
  }
}

async function fetchUsersFromBackend() {
  try {
    const response = await fetch('http://127.0.0.1:8000/api/vms/users');
    if (response.ok) ALL_USERS = await response.json();
  } catch (e) {}
}

async function doLogin() {
  const userInp = document.getElementById('loginUser').value.trim();
  const passInp = document.getElementById('loginPass').value.trim();
  if(!userInp || !passInp) return alert("Vui lòng điền đầy đủ tài khoản và mật khẩu!");

  try {
    const response = await fetch(`http://127.0.0.1:8000/api/auth/token?user=${encodeURIComponent(userInp)}&text_pass=${encodeURIComponent(passInp)}`, { method: 'POST' });
    if (response.ok) {
      const data = await response.json();
      localStorage.setItem('token', data.access_token);
      localStorage.setItem('currentUser', JSON.stringify(data.user));
      window.location.href = 'index.html';
    } else {
      const err = await response.json(); alert(err.detail || "Đăng nhập thất bại!");
    }
  } catch (error) { alert("Không thể kết nối đến Backend FastAPI!"); }
}

function togglePasswordVisibility() {
  const passInput = document.getElementById('loginPass');
  const eyeIcon = document.querySelector('.toggle-eye');
  if (passInput.type === 'password') {
    passInput.type = 'text'; eyeIcon.style.color = 'var(--blue-600)';
  } else {
    passInput.type = 'password'; eyeIcon.style.color = 'var(--slate-400)';
  }
}

async function loginWithGoogleDemo() {
  const emailInput = prompt("MÔ PHỎNG ĐĂNG NHẬP GOOGLE:\\nVui lòng nhập địa chỉ Email Google của bạn:");
  if (!emailInput) return;
  try {
    const response = await fetch(`http://127.0.0.1:8000/api/auth/google-mock?email=${encodeURIComponent(emailInput.trim())}`, { method: 'POST' });
    if (response.ok) {
      const data = await response.json();
      localStorage.setItem('token', data.access_token);
      localStorage.setItem('currentUser', JSON.stringify(data.user));
      alert(`Xác thực Google thành công! Xin chào ${data.user.name}.`);
      window.location.href = 'index.html';
    } else {
      const err = await response.json(); alert(err.detail || "Đăng nhập thất bại!");
    }
  } catch (e) { alert("Lỗi kết nối Backend!"); }
}

function checkAuthSecurity() {
  const currentUser = JSON.parse(localStorage.getItem('currentUser'));
  if (!currentUser) {
    if (!window.location.href.includes('login.html')) window.location.href = 'login.html';
    return false;
  }
  const pagePermissionMap = {
    'cammgmt.html': 'cammgmt', 'users.html': 'usermgmt', 'alerts.html': 'alertmgmt',
    'reports.html': 'reports', 'settings.html': 'sysconfig', 'live.html': 'live',
    'index.html': 'live', 'ai.html': 'live', 'map.html': 'live'
  };
  const userPermissions = currentUser.permissions || [];
  const isAdmin = currentUser.role === 'Quản trị viên';
  Object.keys(pagePermissionMap).forEach(page => {
    const menuLink = document.querySelector(`a[href="${page}"]`);
    if (menuLink) {
      const requiredPerm = pagePermissionMap[page];
      if (page === 'users.html') {
        if (!isAdmin) menuLink.style.display = 'none';
      } else {
        if (!isAdmin && !userPermissions.includes(requiredPerm)) menuLink.style.display = 'none';
      }
    }
  });
  const currentFile = window.location.pathname.split('/').pop() || 'index.html';
  if (pagePermissionMap[currentFile]) {
    const requiredPerm = pagePermissionMap[currentFile];
    if (currentFile === 'users.html' && !isAdmin) {
      alert("🔒 TỪ CHỐI TRUY CẬP: Bạn không có thẩm quyền cấu hình mục Người dùng!");
      window.location.href = 'live.html'; return false;
    }
    if (!isAdmin && !userPermissions.includes(requiredPerm)) {
      alert(`🔒 TỪ CHỐI TRUY CẬP: Tài khoản của bạn không được cấp quyền sử dụng tab này!`);
      if (userPermissions.includes('live')) window.location.href = 'live.html';
      else { localStorage.removeItem('currentUser'); window.location.href = 'login.html'; }
      return false;
    }
  }
  return true;
}

function renderLiveGrid() {
  const liveGrid = document.getElementById('liveCamGrid');
  if (!liveGrid) return;
  liveGrid.innerHTML = '';
  const listToRender = ALL_CAMERAS.slice(0, currentGridLimit);
  listToRender.forEach((cam, i) => {
    const isSelected = i === 0 ? 'selected' : '';
    const mediaHTML = (cam.status === 'online')
      ? (cam.type === 'video' ? `<iframe src="http://127.0.0.1:1984/stream.html?src=${cam.id}&mode=webrtc" frameborder="0" scrolling="no" style="width:100%; height:100%; pointer-events:none; display:block;"></iframe>` : `<img src="${cam.src}" style="width:100%; height:100%; object-fit:fill; pointer-events:none; display:block;">`)
      : `<div style="width:100%; height:100%; background:#0f172a;"></div>`;
      
    let statusOverlayHTML = '';
    if (cam.status !== 'online') {
      statusOverlayHTML = `<div class="cam-status-overlay" style="position:absolute; top:0; left:0; width:100%; height:100%; background:rgba(15,23,42,0.94); display:flex; flex-direction:column; align-items:center; justify-content:center; color:#f87171; z-index:4; gap:8px; text-align:center; padding:12px; backdrop-filter: blur(1px);"><svg width="24" height="24" viewBox="0 0 24 24" fill="none" stroke="currentColor" stroke-width="2" style="margin-bottom:2px;"><path d="m1 1 22 22M16.74 11.24a6 6 0 0 1 0 8.49M14 14a3 3 0 0 1 0 4.24M8.18 8.18a6 6 0 0 0 0 8.49M10.24 10.24a3 3 0 0 0 0 4.24"/></svg><div style="font-weight:700; font-size:12.5px; letter-spacing:0.5px;">MẤT TÍN HIỆU STREAM</div><div style="font-size:11px; color:#94a3b8; font-weight:400;">Thiết bị ngoại tuyến hoặc sai thông số IP: ${cam.ip}</div></div>`;
    }
    const tagVisible = cam.status === 'online';
    const tileHTML = `<div class="live-tile ${isSelected}" data-id="${cam.id}" onclick="selectCam(this)" ondblclick="maximizeCam(this)" style="position:relative; cursor:pointer;"><div class="cam-media-container" style="width:100%; height:100%; pointer-events:none;">${mediaHTML}</div>${statusOverlayHTML}<div class="cam-tag" style="${cam.tag === 'Live' ? 'background:#dc2626' : ''}; z-index:10; pointer-events:none; ${tagVisible ? '' : 'display:none;'}">${cam.tag}</div><div class="cam-label" style="z-index:10; pointer-events:none;">● ${cam.index}. ${cam.name}</div></div>`;
    // <div class="cam-time" style="z-index:10; pointer-events:none;">${new Date().toLocaleTimeString()}</div>
    liveGrid.insertAdjacentHTML('beforeend', tileHTML);
  });
  const onlineCount = ALL_CAMERAS.filter(c => c.status === 'online').length;
  if(document.getElementById('counterToolbar')) document.getElementById('counterToolbar').innerHTML = `Hiển thị ${listToRender.length}/${ALL_CAMERAS.length} camera · <span style="color:var(--green-600);">● ${onlineCount} camera đang hoạt động</span>`;
  const firstTile = liveGrid.querySelector('.live-tile');
  if (firstTile) selectCam(firstTile);
}

function renderDashboardStats() {
  const totalEl = document.getElementById('statTotalCameras');
  if (!totalEl) return; // Không ở trang index.html thì bỏ qua
  if (document.getElementById('statAlertsToday')) return; // Dashboard mới dùng API /api/dashboard/summary

  const total = ALL_CAMERAS.length;
  const onlineCount = ALL_CAMERAS.filter(c => c.status === 'online').length;
  const percent = total > 0 ? Math.round((onlineCount / total) * 100) : 0;
  // Đếm số khu vực (zone) không trùng lặp, dùng phần trước dấu "/" nếu có (VD: "Tòa nhà A / Huyện 1")
  const zoneSet = new Set(ALL_CAMERAS.map(c => (c.zone || '').trim()).filter(z => z));

  document.getElementById('statTotalCameras').textContent = total;
  document.getElementById('statTotalCamerasSub').textContent = `Tất cả: ${total}`;

  document.getElementById('statOnlineCameras').textContent = onlineCount;
  document.getElementById('statOnlinePercent').textContent = `${percent}%`;

  const zoneCountEl = document.getElementById('statZoneCount');
  if (zoneCountEl) zoneCountEl.textContent = zoneSet.size;
}

function formatDashboardBytes(value) {
  const bytes = Number(value || 0);
  if (bytes < 1024) return `${bytes} B`;
  if (bytes < 1024 * 1024) return `${(bytes / 1024).toFixed(1)} KB`;
  if (bytes < 1024 * 1024 * 1024) return `${(bytes / 1024 / 1024).toFixed(1)} MB`;
  return `${(bytes / 1024 / 1024 / 1024).toFixed(2)} GB`;
}

function setText(id, value) {
  const el = document.getElementById(id);
  if (el) el.textContent = value;
}

async function fetchDashboardJson(path) {
  const response = await fetch(`http://127.0.0.1:8000${path}`);
  if (!response.ok) {
    const err = await response.json().catch(() => ({}));
    throw new Error(err.detail || `Không thể tải ${path}`);
  }
  return response.json();
}

async function refreshDashboard() {
  if (!document.getElementById('statTotalCameras')) return;
  try {
    const [summary, activity, system] = await Promise.all([
      fetchDashboardJson('/api/dashboard/summary'),
      fetchDashboardJson('/api/dashboard/activity?limit=8'),
      fetchDashboardJson('/api/dashboard/system')
    ]);
    renderDashboardSummary(summary, system);
    renderDashboardActivity(activity.items || []);
  } catch (error) {
    console.error('Lỗi tải Dashboard:', error);
  }
}

function renderDashboardSummary(summary, system) {
  const camera = summary.camera || {};
  const recording = summary.recording || {};
  const users = summary.users || {};
  const disk = (system && system.disk) || summary.disk || {};
  const alertsToday = Number(summary.alerts?.today || 0);
  const aiToday = Number(summary.ai_events?.today || 0);
  const totalCamera = Number(camera.total || 0);
  const onlineCamera = Number(camera.online || 0);
  const offlineCamera = Number(camera.offline || 0);
  const onlinePercent = totalCamera ? Math.round((onlineCamera / totalCamera) * 100) : 0;

  setText('statTotalCameras', totalCamera);
  setText('statTotalCamerasSub', `Online ${onlineCamera} · Offline ${offlineCamera}`);
  setText('statOnlineCameras', onlineCamera);
  setText('statOnlinePercent', `${onlinePercent}%`);
  setText('statAlertsToday', alertsToday);
  setText('statAlertsSub', alertsToday ? 'Dữ liệu từ PostgreSQL' : 'Chưa có dữ liệu');
  setText('statAiToday', aiToday);
  setText('statAiSub', aiToday ? 'Dữ liệu từ PostgreSQL' : 'Chưa có dữ liệu');
  setText('statZoneCount', Number(summary.areas?.total || 0));
  setText('statZoneSub', 'Khu vực trong PostgreSQL');
  setText('statRecordingEnabled', Number(camera.recording_enabled || 0));
  setText('statRecordingEnabledSub', 'Camera bat_ghi_hinh=true');
  setText('statRecordingToday', Number(recording.today_segments || 0));
  setText('statRecordingTodaySub', `${formatDashboardBytes(recording.today_size)} hôm nay`);
  setText('statRecordingSize', formatDashboardBytes(recording.total_size));
  setText('statRecordingSizeSub', `${Number(recording.total_segments || 0)} segment`);
  setText('statUsersOnline', Number(users.online || 0));
  setText('statUsersSub', `${Number(users.total || 0)} người dùng · ${Number(users.online || 0) ? 'Đang hoạt động' : 'Chưa có dữ liệu phiên'}`);
  setText('statDiskUsage', `${Math.round(Number(disk.percent || 0))}%`);
  setText('statDiskSub', `${formatDashboardBytes(disk.used)} / ${formatDashboardBytes(disk.total)}`);
  setText('quickAlertToday', alertsToday);
  setText('quickAiToday', aiToday);
  setText('quickRecordingToday', Number(recording.today_segments || 0));
  setText('quickUserOnline', Number(users.online || 0));

  renderEmptyAiTable(aiToday);
  renderLineChart('alertChartHost', summary.charts?.alerts_7_days || [], '#3b82f6');
  renderLineChart('aiChartHost', summary.charts?.ai_events_7_days || [], '#8b5cf6');
}

function renderEmptyAiTable(aiToday) {
  const table = document.getElementById('recentAiTable');
  if (!table) return;
  if (!aiToday) {
    table.innerHTML = '<tr><th>Thời gian</th><th>Camera</th><th>Sự kiện</th><th>Mức độ</th><th>Trạng thái</th></tr><tr><td colspan="5" style="text-align:center;color:var(--slate-400);padding:24px;">Chưa có dữ liệu</td></tr>';
  }
}

function renderLineChart(hostId, rows, color) {
  const host = document.getElementById(hostId);
  if (!host) return;
  const values = (rows || []).map(row => Number(row.total || 0));
  const hasData = values.some(value => value > 0);
  if (!hasData) {
    host.innerHTML = 'Chưa có dữ liệu';
    host.style.display = 'flex';
    return;
  }
  const max = Math.max(...values, 1);
  const width = 560;
  const height = 190;
  const points = values.map((value, index) => {
    const x = 20 + index * ((width - 40) / Math.max(values.length - 1, 1));
    const y = 20 + (height - 40) * (1 - value / max);
    return `${x},${y}`;
  }).join(' ');
  const labels = (rows || []).map((row, index) => {
    const x = 20 + index * ((width - 40) / Math.max(values.length - 1, 1));
    const label = String(row.day || '').slice(5);
    return `<text x="${x - 16}" y="184" font-size="11" fill="#94a3b8">${label}</text>`;
  }).join('');
  host.style.display = 'block';
  host.innerHTML = `<svg viewBox="0 0 ${width} ${height}" style="width:100%;height:220px;padding:10px 0;"><polyline points="${points}" fill="none" stroke="${color}" stroke-width="2.5"/>${labels}</svg>`;
}

function renderDashboardActivity(items) {
  const host = document.getElementById('dashboardActivityList');
  if (!host) return;
  if (!items.length) {
    host.style.display = 'flex';
    host.innerHTML = 'Chưa có dữ liệu';
    return;
  }
  host.style.display = 'block';
  host.innerHTML = '';
  items.forEach(item => {
    const time = item.occurred_at ? new Date(item.occurred_at).toLocaleTimeString('vi-VN', { hour12: false }) : '-';
    const html = `<div class="activity-item"><div class="act-time">${time}</div><div class="act-icon" style="background:var(--blue-50);color:var(--blue-600)"><svg width="16" height="16" viewBox="0 0 24 24" fill="none" stroke="currentColor" stroke-width="2"><circle cx="12" cy="12" r="9"/></svg></div><div class="act-body"><div class="t">${item.title || item.action || '-'}</div><div class="s">${item.source || 'log'} · ${item.subtitle || ''}</div></div><div class="act-loc">${item.target || '-'}</div></div>`;
    host.insertAdjacentHTML('beforeend', html);
  });
}

function renderOverviewGrid() {
  const overviewGrid = document.getElementById('overviewCamGrid');
  if (!overviewGrid) return;
  overviewGrid.innerHTML = '';
  const listToRender = ALL_CAMERAS.slice(0, 4);
  listToRender.forEach((cam) => {
    const mediaHTML = (cam.status === 'online')
      ? (cam.type === 'video' ? `<iframe src="http://127.0.0.1:1984/stream.html?src=${cam.id}&mode=webrtc" frameborder="0" scrolling="no" style="width:100%; height:100%; pointer-events:none; display:block;"></iframe>` : `<img src="${cam.src}" style="width:100%; height:100%; object-fit:fill; pointer-events:none; display:block;">`)
      : `<div style="width:100%; height:100%; background:#0f172a;"></div>`;
    let statusOverlayHTML = '';
    if (cam.status !== 'online') {
      statusOverlayHTML = `<div class="cam-status-overlay" style="position:absolute; top:0; left:0; width:100%; height:100%; background:rgba(15,23,42,0.94); display:flex; flex-direction:column; align-items:center; justify-content:center; color:#f87171; z-index:4; gap:6px; text-align:center; padding:10px; backdrop-filter: blur(1px);"><svg width="20" height="24" viewBox="0 0 24 24" fill="none" stroke="currentColor" stroke-width="2"><path d="m1 1 22 22M16.74 11.24a6 6 0 0 1 0 8.49M14 14a3 3 0 0 1 0 4.24M8.18 8.18a6 6 0 0 0 0 8.49M10.24 10.24a3 3 0 0 0 0 4.24"/></svg><div style="font-weight:700; font-size:11.5px;">MẤT KẾT NỐI</div></div>`;
    }
    const tileHTML = `<div class="cam-tile" data-id="${cam.id}" ondblclick="maximizeCam(this)" style="position:relative; cursor:pointer;"><div class="cam-media-container" style="width:100%; height:100%; pointer-events:none;">${mediaHTML}</div>${statusOverlayHTML}<div class="cam-label" style="z-index:10; pointer-events:none;">● ${cam.index}. ${cam.name}</div></div>`; 
    // <div class="cam-time" style="z-index:10; pointer-events:none;">${new Date().toLocaleTimeString()}</div>
    overviewGrid.insertAdjacentHTML('beforeend', tileHTML);
  });
}

function renderCamManagementTable() {
  const tbody = document.getElementById('camManagementTableBody');
  if (!tbody) return;
  tbody.innerHTML = '';
  ALL_CAMERAS.forEach((cam) => {
    const trHTML = `<tr><td><b>#${cam.index}</b></td><td><span style="font-weight:600; color:var(--slate-900);">${cam.name}</span></td><td><span class="pill thap" style="font-family:monospace;">${cam.ip}</span></td><td>${cam.model}</td><td>${cam.zone}</td><td>${cam.loc}</td><td><span class="status daxuly"><span class="d"></span>${cam.status === 'online' ? 'Hoạt động' : 'Ngoại tuyến'}</span></td><td><div style="display:flex; gap:6px; align-items:center;"><button class="btn-sm" onclick="openEditCamModal('${cam.id}')" style="width:auto; padding:5px 12px; display:inline-block; cursor:pointer;">Sửa</button><button class="btn-sm red" onclick="deleteCamera('${cam.id}')" style="width:auto; padding:5px 12px; display:inline-block; cursor:pointer;">Xóa</button></div></td></tr>`;
    tbody.insertAdjacentHTML('beforeend', trHTML);
  });
}

async function handleAddCamera(event) {
  event.preventDefault();
  const payload = editingCameraId ? getCameraUpdatePayload(editingCameraId) : getCameraFormPayload(false);
  if (editingCameraId && Object.keys(payload).length === 0) {
    alert("Chưa có thông tin camera nào được thay đổi.");
    return;
  }
  const submitBtn = event.target.querySelector('button[type="submit"]');
  const originalText = submitBtn.textContent;
  submitBtn.textContent = editingCameraId ? "Đang lưu thay đổi..." : "Đang kết nối & khởi động lại...";
  submitBtn.disabled = true;
  try {
    const url = editingCameraId
      ? `http://127.0.0.1:8000/api/vms/camera/${editingCameraId}`
      : 'http://127.0.0.1:8000/api/vms/camera/add';
    const response = await fetch(url, {
      method: editingCameraId ? 'PUT' : 'POST',
      headers: { 'Content-Type': 'application/json' },
      body: JSON.stringify(payload)
    });
    if (response.ok) {
      closeAddCamModal();
      document.getElementById('addCamForm').reset();
      await fetchCamerasFromBackend();
      renderCamManagementTable();
    } else {
      const err = await response.json().catch(() => ({}));
      alert(err.detail || (editingCameraId ? "Không thể cập nhật camera." : "Lỗi ghi nhận cấu hình từ Backend."));
    }
  } catch (error) {
    alert("Không thể kết nối API tới FastAPI.");
  } finally {
    submitBtn.textContent = originalText;
    submitBtn.disabled = false;
  }
}

async function deleteCamera(id) {
  if (confirm('Bạn có chắc chắn muốn xóa camera này ra khỏi hệ thống?')) {
    try {
      const response = await fetch(`http://127.0.0.1:8000/api/vms/camera/${id}`, { method: 'DELETE' });
      if (response.ok) { await fetchCamerasFromBackend(); renderCamManagementTable(); }
    } catch (error) { alert("Lỗi khi kết nối yêu cầu xóa."); }
  }
}

function getCameraFormPayload(isEdit = false) {
  const payload = {
    name: document.getElementById('modalCamName').value.trim(),
    ip: document.getElementById('modalCamIP').value.trim(),
    user: document.getElementById('modalCamUser').value.trim(),
    model: document.getElementById('modalCamModel').value.trim(),
    zone: document.getElementById('modalCamZone').value.trim(),
    loc: document.getElementById('modalCamLoc').value.trim()
  };
  const password = document.getElementById('modalCamPass').value.trim();
  if (password || !isEdit) payload.password = password;

  const optionalTextFields = [
    ['resolution', 'modalCamResolution'],
    ['codec', 'modalCamCodec']
  ];
  optionalTextFields.forEach(([key, id]) => {
    const value = document.getElementById(id).value.trim();
    if (value) payload[key] = value;
  });

  const optionalNumberFields = [
    ['fps', 'modalCamFPS'],
    ['bitrate', 'modalCamBitrate'],
    ['lat', 'modalCamLat'],
    ['lng', 'modalCamLng']
  ];
  optionalNumberFields.forEach(([key, id]) => {
    const value = document.getElementById(id).value.trim();
    if (value !== '') payload[key] = Number(value);
  });
  return payload;
}

function getCameraUpdatePayload(camId) {
  const cam = ALL_CAMERAS.find(c => c.id === camId);
  const fullPayload = getCameraFormPayload(true);
  const updatePayload = {};
  const comparableFields = [
    ['name', cam?.name],
    ['ip', cam?.ip],
    ['model', cam?.model],
    ['zone', cam?.zone],
    ['loc', cam?.loc],
    ['resolution', cam?.resolution],
    ['codec', cam?.codec],
    ['fps', cam?.fps],
    ['bitrate', cam?.bitrate],
    ['lat', cam?.lat],
    ['lng', cam?.lng]
  ];
  comparableFields.forEach(([key, currentValue]) => {
    if (!(key in fullPayload)) return;
    if (String(fullPayload[key] ?? '') !== String(currentValue ?? '')) {
      updatePayload[key] = fullPayload[key];
    }
  });

  const password = document.getElementById('modalCamPass').value.trim();
  if (password) {
    updatePayload.password = password;
    const user = document.getElementById('modalCamUser').value.trim();
    if (user) updatePayload.user = user;
    const ip = document.getElementById('modalCamIP').value.trim();
    if (ip) updatePayload.ip = ip;
  }
  return updatePayload;
}

function fillCameraForm(cam) {
  document.getElementById('modalCamName').value = cam.name || '';
  document.getElementById('modalCamIP').value = cam.ip || '';
  document.getElementById('modalCamModel').value = cam.model || '';
  document.getElementById('modalCamUser').value = 'admin';
  document.getElementById('modalCamPass').value = '';
  document.getElementById('modalCamZone').value = cam.zone || '';
  document.getElementById('modalCamLoc').value = cam.loc || '';
  document.getElementById('modalCamResolution').value = cam.resolution || '';
  document.getElementById('modalCamCodec').value = cam.codec || '';
  document.getElementById('modalCamFPS').value = cam.fps || '';
  document.getElementById('modalCamBitrate').value = cam.bitrate || '';
  document.getElementById('modalCamLat').value = cam.lat ?? '';
  document.getElementById('modalCamLng').value = cam.lng ?? '';
}

function setCameraModalMode(mode) {
  const isEdit = mode === 'edit';
  const title = document.getElementById('camModalTitle');
  const submitBtn = document.getElementById('camModalSubmitBtn');
  const passwordInput = document.getElementById('modalCamPass');
  if (title) title.textContent = isEdit ? 'Sửa thông tin camera' : 'Quy hoạch Thiết Bị Camera Động';
  if (submitBtn) submitBtn.textContent = isEdit ? 'Lưu thay đổi' : 'Kích hoạt kết nối';
  if (passwordInput) {
    passwordInput.required = !isEdit;
    passwordInput.placeholder = isEdit ? 'Để trống nếu không đổi mật khẩu RTSP' : 'Nhập mật khẩu camera';
  }
}

function openAddCamModal() {
  editingCameraId = null;
  const form = document.getElementById('addCamForm');
  if (form) form.reset();
  document.getElementById('modalCamUser').value = 'admin';
  setCameraModalMode('add');
  const modal = document.getElementById('addCamModal');
  if (modal) modal.style.display = 'flex';
}

function openEditCamModal(camId) {
  const cam = ALL_CAMERAS.find(c => c.id === camId);
  if (!cam) {
    alert("Không tìm thấy camera cần sửa.");
    return;
  }
  editingCameraId = camId;
  setCameraModalMode('edit');
  fillCameraForm(cam);
  const modal = document.getElementById('addCamModal');
  if (modal) modal.style.display = 'flex';
}

function closeAddCamModal() {
  const modal = document.getElementById('addCamModal');
  if (modal) modal.style.display = 'none';
  editingCameraId = null;
  const form = document.getElementById('addCamForm');
  if (form) form.reset();
  setCameraModalMode('add');
}
function changeGridLimit(val) { currentGridLimit = parseInt(val); const liveGrid = document.getElementById('liveCamGrid'); if(liveGrid) { liveGrid.style.gridTemplateColumns = (currentGridLimit === 2 || currentGridLimit === 4) ? 'repeat(2, 1fr)' : 'repeat(3, 1fr)'; renderLiveGrid(); } }
function selectCam(el) {
  document.querySelectorAll('.live-tile').forEach(t => t.classList.remove('selected'));
  el.classList.add('selected');
  const camId = el.getAttribute('data-id');
  const camData = ALL_CAMERAS.find(c => c.id === camId);
  if (!camData) return;
  selectedLiveCameraId = camId;
  if(document.getElementById('sideCamName')) document.getElementById('sideCamName').textContent = `${camData.index}. ${camData.name}`;
  if(document.getElementById('sideCamIP')) document.getElementById('sideCamIP').textContent = camData.ip;
  if(document.getElementById('sideCamModel')) document.getElementById('sideCamModel').textContent = camData.model;
  if(document.getElementById('sideCamZone')) document.getElementById('sideCamZone').textContent = camData.zone;
  if(document.getElementById('sideCamLoc')) document.getElementById('sideCamLoc').textContent = camData.loc;
  const detailContainer = document.getElementById('mainDetailContainer');
  if (detailContainer) {
    detailContainer.innerHTML = `<iframe src="http://127.0.0.1:1984/stream.html?src=${camData.id}&mode=webrtc" frameborder="0" scrolling="no" style="width:100%; aspect-ratio:16/9; pointer-events:none; display:block;"></iframe>`;
  }
}
function maximizeCam(el) { const mediaElement = el.querySelector('iframe') || el.querySelector('img'); if (mediaElement && mediaElement.requestFullscreen) mediaElement.requestFullscreen(); }

function handleLiveQueryAction() {
  if (!document.getElementById('liveCamGrid')) return;
  const params = new URLSearchParams(window.location.search);
  const cameraId = params.get('camera_id');
  const action = params.get('action') || 'live';
  if (!cameraId) return;

  let tile = Array.from(document.querySelectorAll('.live-tile')).find(item => item.dataset.id === cameraId);
  if (!tile && ALL_CAMERAS.some(cam => cam.id === cameraId)) {
    currentGridLimit = ALL_CAMERAS.length;
    const gridSelect = document.getElementById('gridLimitSelect');
    if (gridSelect) gridSelect.value = String(currentGridLimit);
    renderLiveGrid();
    tile = Array.from(document.querySelectorAll('.live-tile')).find(item => item.dataset.id === cameraId);
  }
  if (!tile) return;
  selectCam(tile);
  tile.scrollIntoView({ behavior: 'smooth', block: 'center', inline: 'center' });

  if (action === 'playback') {
    openPlaybackModal();
    const playbackSelect = document.getElementById('playbackCameraSelect');
    if (playbackSelect && ALL_CAMERAS.some(cam => cam.id === cameraId)) {
      playbackSelect.value = cameraId;
    }
  }
}

function formatDateTimeLocal(date) {
  const pad = (value) => String(value).padStart(2, '0');
  return `${date.getFullYear()}-${pad(date.getMonth() + 1)}-${pad(date.getDate())}T${pad(date.getHours())}:${pad(date.getMinutes())}`;
}

function formatPlaybackTime(value) {
  if (!value) return '-';
  return new Date(value).toLocaleString('vi-VN', { hour12: false });
}

function formatPlaybackBytes(value) {
  const bytes = Number(value || 0);
  if (bytes < 1024) return `${bytes} B`;
  if (bytes < 1024 * 1024) return `${(bytes / 1024).toFixed(1)} KB`;
  if (bytes < 1024 * 1024 * 1024) return `${(bytes / 1024 / 1024).toFixed(1)} MB`;
  return `${(bytes / 1024 / 1024 / 1024).toFixed(1)} GB`;
}

function setPlaybackStatus(message, color = 'var(--slate-500)') {
  const status = document.getElementById('playbackStatus');
  if (status) {
    status.textContent = message;
    status.style.color = color;
  }
}

function populatePlaybackCameraSelect() {
  const select = document.getElementById('playbackCameraSelect');
  if (!select) return;
  select.innerHTML = '';
  ALL_CAMERAS.forEach(cam => {
    const option = document.createElement('option');
    option.value = cam.id;
    option.textContent = `${cam.index || ''}. ${cam.name || cam.id}`.trim();
    select.appendChild(option);
  });
  if (selectedLiveCameraId && ALL_CAMERAS.some(cam => cam.id === selectedLiveCameraId)) {
    select.value = selectedLiveCameraId;
  }
}

function openPlaybackModal() {
  const modal = document.getElementById('playbackModal');
  if (!modal) return;
  populatePlaybackCameraSelect();
  const now = new Date();
  const oneHourAgo = new Date(now.getTime() - 60 * 60 * 1000);
  const fromInput = document.getElementById('playbackFromTime');
  const toInput = document.getElementById('playbackToTime');
  if (fromInput && !fromInput.value) fromInput.value = formatDateTimeLocal(oneHourAgo);
  if (toInput && !toInput.value) toInput.value = formatDateTimeLocal(now);
  modal.style.display = 'flex';
  setPlaybackStatus('Chọn khoảng thời gian để tìm video đã lưu.');
}

function closePlaybackModal() {
  const modal = document.getElementById('playbackModal');
  const video = document.getElementById('playbackVideo');
  if (video) {
    video.pause();
    video.removeAttribute('src');
    video.load();
  }
  if (modal) modal.style.display = 'none';
}

async function searchPlaybackSegments() {
  const cameraSelect = document.getElementById('playbackCameraSelect');
  const fromInput = document.getElementById('playbackFromTime');
  const toInput = document.getElementById('playbackToTime');
  const cameraId = cameraSelect?.value;
  if (!cameraId) {
    setPlaybackStatus('Chưa có camera để tìm phát lại.', '#dc2626');
    return;
  }
  if (!fromInput?.value || !toInput?.value) {
    setPlaybackStatus('Vui lòng chọn đủ từ thời gian và đến thời gian.', '#dc2626');
    return;
  }
  const fromDate = new Date(fromInput.value);
  const toDate = new Date(toInput.value);
  if (Number.isNaN(fromDate.getTime()) || Number.isNaN(toDate.getTime()) || fromDate >= toDate) {
    setPlaybackStatus('Khoảng thời gian không hợp lệ.', '#dc2626');
    return;
  }

  setPlaybackStatus('Đang tìm video phát lại...', 'var(--blue-600)');
  const params = new URLSearchParams({
    camera_id: cameraId,
    from_time: fromDate.toISOString(),
    to_time: toDate.toISOString()
  });

  try {
    const response = await fetch(`http://127.0.0.1:8000/api/playback/search?${params.toString()}`);
    if (!response.ok) {
      const err = await response.json().catch(() => ({}));
      throw new Error(err.detail || 'Không thể tìm video phát lại.');
    }
    const data = await response.json();
    PLAYBACK_SEGMENTS = data.segments || [];
    PLAYBACK_GAPS = data.gaps || [];
    renderPlaybackTimeline(PLAYBACK_SEGMENTS, PLAYBACK_GAPS);
    renderPlaybackSegmentList(PLAYBACK_SEGMENTS);
    if (PLAYBACK_SEGMENTS.length > 0) {
      setPlaybackStatus(`Tìm thấy ${PLAYBACK_SEGMENTS.length} segment, ${PLAYBACK_GAPS.length} khoảng trống.`);
    } else {
      setPlaybackStatus('Không có video trong khoảng thời gian đã chọn.', 'var(--slate-500)');
    }
  } catch (error) {
    PLAYBACK_SEGMENTS = [];
    PLAYBACK_GAPS = [];
    renderPlaybackTimeline([], []);
    renderPlaybackSegmentList([]);
    setPlaybackStatus(error.message || 'Lỗi kết nối API phát lại.', '#dc2626');
  }
}

function renderPlaybackTimeline(segments, gaps) {
  const timeline = document.getElementById('playbackTimeline');
  if (!timeline) return;
  timeline.innerHTML = '';
  if (!segments.length) {
    timeline.innerHTML = '<div style="height:100%; display:flex; align-items:center; justify-content:center; color:var(--slate-400); font-size:13px;">Không có dữ liệu timeline</div>';
    return;
  }

  const times = [];
  segments.forEach(seg => {
    times.push(new Date(seg.start_time).getTime(), new Date(seg.end_time).getTime());
  });
  gaps.forEach(gap => {
    const gapStart = gap.start_time || gap.from_time;
    const gapEnd = gap.end_time || gap.to_time;
    times.push(new Date(gapStart).getTime(), new Date(gapEnd).getTime());
  });
  const minTime = Math.min(...times);
  const maxTime = Math.max(...times);
  const total = Math.max(maxTime - minTime, 1000);
  const pct = (value) => Math.max(0, Math.min(100, ((new Date(value).getTime() - minTime) / total) * 100));

  gaps.forEach(gap => {
    const gapStart = gap.start_time || gap.from_time;
    const gapEnd = gap.end_time || gap.to_time;
    const left = pct(gapStart);
    const width = Math.max(0.5, pct(gapEnd) - left);
    const bar = document.createElement('div');
    bar.title = `Gap: ${formatPlaybackTime(gapStart)} - ${formatPlaybackTime(gapEnd)}`;
    bar.style.cssText = `position:absolute; top:12px; left:${left}%; width:${width}%; height:38px; background:#ef4444; opacity:0.28; border-radius:6px;`;
    timeline.appendChild(bar);
  });

  segments.forEach(seg => {
    const left = pct(seg.start_time);
    const width = Math.max(0.55, pct(seg.end_time) - left);
    const bar = document.createElement('button');
    bar.type = 'button';
    bar.className = 'playback-timeline-segment';
    bar.dataset.segmentId = seg.id;
    bar.title = `${formatPlaybackTime(seg.start_time)} - ${formatPlaybackTime(seg.end_time)}`;
    bar.style.cssText = `position:absolute; top:18px; left:${left}%; width:${width}%; height:26px; border:0; background:#16a34a; border-radius:5px; cursor:pointer; box-shadow:0 1px 2px rgba(15,23,42,0.12);`;
    bar.onclick = () => playPlaybackSegment(seg.id);
    timeline.appendChild(bar);
  });
}

function renderPlaybackSegmentList(segments) {
  const list = document.getElementById('playbackSegmentList');
  if (!list) return;
  list.innerHTML = '';
  if (!segments.length) {
    list.innerHTML = '<div style="height:100%; display:flex; align-items:center; justify-content:center; padding:16px; text-align:center; color:var(--slate-400); font-size:13px;">Không có segment phù hợp.</div>';
    return;
  }
  segments.forEach(seg => {
    const item = document.createElement('button');
    item.type = 'button';
    item.className = 'playback-segment-item';
    item.dataset.segmentId = seg.id;
    item.style.cssText = 'width:100%; border:0; border-bottom:1px solid var(--slate-100); background:#fff; padding:10px 12px; text-align:left; cursor:pointer; display:block;';
    item.innerHTML = `
      <div style="display:flex; justify-content:space-between; gap:10px; align-items:center;">
        <strong style="font-size:13px; color:var(--slate-900);">Segment #${seg.id}</strong>
        <span style="font-size:11px; color:var(--slate-500);">${Math.round(Number(seg.duration_seconds || 0))}s</span>
      </div>
      <div style="font-size:12px; color:var(--slate-500); margin-top:5px; line-height:1.45;">
        ${formatPlaybackTime(seg.start_time)}<br>
        ${formatPlaybackTime(seg.end_time)}
      </div>
      <div style="font-size:11px; color:var(--slate-400); margin-top:5px;">${formatPlaybackBytes(seg.size_bytes)} · ${seg.status || 'READY'}</div>
    `;
    item.onclick = () => playPlaybackSegment(seg.id);
    list.appendChild(item);
  });
}

function playPlaybackSegment(segmentId) {
  const video = document.getElementById('playbackVideo');
  if (!video) return;
  document.querySelectorAll('.playback-segment-item').forEach(item => {
    const active = item.dataset.segmentId === String(segmentId);
    item.style.background = active ? 'var(--blue-50)' : '#fff';
    item.style.boxShadow = active ? 'inset 3px 0 0 var(--blue-600)' : 'none';
  });
  document.querySelectorAll('.playback-timeline-segment').forEach(item => {
    item.style.background = item.dataset.segmentId === String(segmentId) ? '#2563eb' : '#16a34a';
  });
  video.src = `http://127.0.0.1:8000/api/playback/file/${segmentId}`;
  video.load();
  video.play().catch(() => {});
  setPlaybackStatus(`Đang phát segment #${segmentId}.`);
}

function renderUserTable() {
  const userBody = document.getElementById('userTableBody');
  if (!userBody) return;
  userBody.innerHTML = '';
  ALL_USERS.forEach((user) => {
    const initials = user.name.split(' ').map(n => n[0]).join('').slice(-2).toUpperCase();
    const roleClass = user.role === 'Quản trị viên' ? 'admin' : (user.role === 'Giám sát' ? 'giamsat' : 'nhanvien');
    const statusClass = user.status === 'Hoạt động' ? 'daxuly' : 'dangxuly';
    const isRowSelected = user.username === selectedUsername ? 'style="background:var(--blue-50);"' : '';
    const rowHTML = `<tr ${isRowSelected} onclick="selectUserAccount('${user.username}')" style="cursor:pointer;"><td style="display:flex;align-items:center;gap:8px;"><div class="user-row-avatar" style="background:linear-gradient(135deg,#3b82f6,#8b5cf6);color:#fff;font-weight:700;display:flex;align-items:center;justify-content:center;width:34px;height:34px;border-radius:50%;">${initials}</div><div><strong>${user.name}</strong><br><span style="color:var(--slate-400);font-size:11px;">${user.username}</span></div></td><td><span class="role-pill ${roleClass}">${user.role}</span></td><td>${user.unit}</td><td>${user.email}</td><td><span class="status ${statusClass}"><span class="d"></span>${user.status}</span></td></tr>`;
    userBody.insertAdjacentHTML('beforeend', rowHTML);
  });
}

function selectUserAccount(username) {
  selectedUsername = username;
  const user = ALL_USERS.find(u => u.username === username);
  if (!user) return;
  renderUserTable(); 
  const currentUser = JSON.parse(localStorage.getItem('currentUser')) || {};
  const isAdmin = currentUser.role === 'Quản trị viên';
  document.getElementById('userDetailPanel').style.display = 'block';
  document.getElementById('txtDetailName').textContent = user.name;
  document.getElementById('txtDetailUsername').textContent = `@${user.username}`;
  document.getElementById('txtDetailRole').textContent = user.role;
  document.getElementById('txtDetailUnit').textContent = user.unit;
  document.getElementById('detailAvatar').textContent = user.name.split(' ').map(n => n[0]).join('').slice(-2).toUpperCase();
  const statusLbl = document.getElementById('lblDetailStatus');
  statusLbl.textContent = user.status;
  if(user.status === 'Hoạt động') { statusLbl.style.background = '#dcfce7'; statusLbl.style.color = '#16a34a'; document.getElementById('btnLockAccount').textContent = "🔒 Khóa tài khoản"; } 
  else { statusLbl.style.background = '#fee2e2'; statusLbl.style.color = '#dc2626'; document.getElementById('btnLockAccount').textContent = "🔓 Mở khóa tài khoản"; }
  document.getElementById('inpDetailName').value = user.name; document.getElementById('inpDetailEmail').value = user.email; document.getElementById('inpDetailPhone').value = user.phone; document.getElementById('inpDetailUnit').value = user.unit; document.getElementById('selDetailRole').value = user.role;
  const permissionsList = ['live', 'playback', 'cammgmt', 'usermgmt', 'alertmgmt', 'reports', 'sysconfig', 'export'];
  permissionsList.forEach(p => { const chk = document.getElementById(`chk_${p}`); if(chk) { chk.checked = user.permissions.includes(p); chk.disabled = !isAdmin; chk.onchange = async () => { if (!isAdmin) return; await saveUserPermissionsInstant(); }; } });
  document.getElementById('inpDetailName').disabled = !isAdmin; document.getElementById('inpDetailEmail').disabled = !isAdmin; document.getElementById('inpDetailPhone').disabled = !isAdmin; document.getElementById('inpDetailUnit').disabled = !isAdmin; document.getElementById('selDetailRole').disabled = !isAdmin;
  const actionButtons = document.querySelectorAll('#userDetailPanel button');
  actionButtons.forEach(btn => { btn.style.display = isAdmin ? 'block' : 'none'; });
  const btnLock = document.getElementById('btnLockAccount'); if(btnLock) btnLock.style.display = isAdmin ? 'inline-block' : 'none';
  const btnReset = btnLock ? btnLock.previousElementSibling : null; if(btnReset) btnReset.style.display = isAdmin ? 'inline-block' : 'none';
}

async function submitNewUser(event) {
  event.preventDefault();
  const token = localStorage.getItem('token');
  const payload = { username: document.getElementById('modalUserUsername').value.trim(), password: document.getElementById('modalUserPassword').value.trim(), name: document.getElementById('modalUserName').value.trim(), role: document.getElementById('modalUserRole').value, unit: document.getElementById('modalUserUnit').value.trim(), email: document.getElementById('modalUserEmail').value.trim(), phone: document.getElementById('modalUserPhone').value.trim() };
  try {
    const response = await fetch('http://127.0.0.1:8000/api/vms/user/add', { method: 'POST', headers: { 'Content-Type': 'application/json', 'Authorization': `Bearer ${token}` }, body: JSON.stringify(payload) });
    if (response.ok) { closeAddUserModal(); document.getElementById('addUserForm').reset(); await fetchUsersFromBackend(); renderUserTable(); alert("Đã quy hoạch cấp tài khoản!"); } 
    else { const err = await response.json(); alert(err.detail || "Lỗi tạo tài khoản mới!"); }
  } catch (e) { alert("Không thể liên kết tới máy chủ Backend!"); }
}

async function saveUserPermissionsInstant() {
  if (!selectedUsername) return;
  const token = localStorage.getItem('token');
  if (!token) return alert("🔒 Phiên làm việc hết hạn, vui lòng đăng nhập lại!");

  const permissionsList = ['live', 'playback', 'cammgmt', 'usermgmt', 'alertmgmt', 'reports', 'sysconfig', 'export'];
  const activePermissions = permissionsList.filter(p => {
    const chk = document.getElementById(`chk_${p}`);
    return chk ? chk.checked : false;
  });

  const payload = {
    name: document.getElementById('inpDetailName').value.trim(),
    email: document.getElementById('inpDetailEmail').value.trim(),
    phone: document.getElementById('inpDetailPhone').value.trim(),
    unit: document.getElementById('inpDetailUnit').value.trim(),
    role: document.getElementById('selDetailRole').value,
    permissions: activePermissions
  };

  try {
    const response = await fetch(`http://127.0.0.1:8000/api/vms/user/${selectedUsername}`, {
      method: 'PUT',
      headers: { 
         'Content-Type': 'application/json',
         'Authorization': `Bearer ${token}`
      },
      body: JSON.stringify(payload)
    });
    if (response.ok) {
      console.log(`Đã tự động cập nhật phân quyền cho @${selectedUsername}:`, activePermissions);
      await fetchUsersFromBackend(); 
    } else {
      const err = await response.json();
      alert("❌ Lỗi cấu hình quyền: " + (err.detail || "Không có thẩm quyền!"));
    }
  } catch (e) { console.error("Lỗi kết nối lưu quyền:", e); }
}

// Được gọi khi bấm nút "💾 Lưu thay đổi" trong users.html - lưu toàn bộ thông tin + quyền hạn, có thông báo kết quả rõ ràng
async function saveUserProfileChanges() {
  if (!selectedUsername) { alert("Vui lòng chọn một người dùng trước!"); return; }
  const token = localStorage.getItem('token');
  if (!token) return alert("🔒 Phiên làm việc hết hạn, vui lòng đăng nhập lại!");

  const permissionsList = ['live', 'playback', 'cammgmt', 'usermgmt', 'alertmgmt', 'reports', 'sysconfig', 'export'];
  const activePermissions = permissionsList.filter(p => {
    const chk = document.getElementById(`chk_${p}`);
    return chk ? chk.checked : false;
  });

  const payload = {
    name: document.getElementById('inpDetailName').value.trim(),
    email: document.getElementById('inpDetailEmail').value.trim(),
    phone: document.getElementById('inpDetailPhone').value.trim(),
    unit: document.getElementById('inpDetailUnit').value.trim(),
    role: document.getElementById('selDetailRole').value,
    permissions: activePermissions
  };

  const btn = document.querySelector('#userDetailPanel button[onclick="saveUserProfileChanges()"]');
  const originalText = btn ? btn.textContent : null;
  if (btn) { btn.textContent = "Đang lưu..."; btn.disabled = true; }

  try {
    const response = await fetch(`http://127.0.0.1:8000/api/vms/user/${selectedUsername}`, {
      method: 'PUT',
      headers: {
        'Content-Type': 'application/json',
        'Authorization': `Bearer ${token}`
      },
      body: JSON.stringify(payload)
    });
    if (response.ok) {
      await fetchUsersFromBackend();
      renderUserTable();
      selectUserAccount(selectedUsername);
      alert("✅ Đã lưu thay đổi thông tin người dùng!");
    } else {
      const err = await response.json();
      alert("❌ Lỗi khi lưu thay đổi: " + (err.detail || "Không có thẩm quyền!"));
    }
  } catch (e) {
    alert("❌ Không thể kết nối tới máy chủ Backend!");
  } finally {
    if (btn) { btn.textContent = originalText; btn.disabled = false; }
  }
}

// 12. Nút ĐẶT LẠI MẬT KHẨU (ĐÃ SỬA LỖI THIẾU HEADERS)
async function resetUserPassword() {
  if (!selectedUsername) return;
  if (!confirm(`Bạn có chắc chắn muốn đặt lại mật khẩu cho tài khoản @${selectedUsername} về mặc định không?`)) return;
  
  const token = localStorage.getItem('token');
  if (!token) return alert("🔒 Vui lòng đăng nhập tài khoản Quản trị viên!");

  try {
    const response = await fetch(`http://127.0.0.1:8000/api/vms/user/${selectedUsername}/reset-password`, { 
       method: 'POST',
       headers: { 
         'Content-Type': 'application/json',
         'Authorization': `Bearer ${token}` 
       }
    });
    if (response.ok) {
      const result = await response.json();
      alert(`🎉 Đặt lại thành công!\nMật khẩu mới của @${selectedUsername} là: ${result.new_password}`);
    } else {
      const err = await response.json(); 
      alert("❌ Từ chối thao tác: " + (err.detail || "Lỗi phân quyền"));
    }
  } catch (e) { alert("Lỗi xử lý yêu cầu đặt lại mật khẩu!"); }
}

// 13. Nút KHÓA / MỞ KHÓA TÀI KHOẢN (ĐÃ FIX KHỚP BACKEND)
async function toggleLockAccount() {
  if (!selectedUsername) return;
  const token = localStorage.getItem('token');
  if (!token) return alert("🔒 Phiên làm việc hết hạn!");

  try {
    const response = await fetch(`http://127.0.0.1:8000/api/vms/user/${selectedUsername}/toggle-lock`, { 
       method: 'POST',
       headers: { 
         'Content-Type': 'application/json',
         'Authorization': `Bearer ${token}` 
       }
    });
    if (response.ok) {
      const result = await response.json();
      alert(`Trạng thái tài khoản đổi thành: [${result.new_status}]`);
      await fetchUsersFromBackend();
      selectUserAccount(selectedUsername); // Vẽ lại giao diện panel phải
    } else {
      const err = await response.json();
      alert("❌ Lỗi: " + (err.detail || "Không thể thao tác trên tài khoản này!"));
    }
  } catch (e) { alert("Lỗi khi kết nối thay đổi trạng thái khóa!"); }
}

async function deleteUserAccount() {
  if (!selectedUsername) return;
  if (!confirm(`Xóa vĩnh viễn tài khoản @${selectedUsername}?`)) return;
  const token = localStorage.getItem('token');
  try {
    const response = await fetch(`http://127.0.0.1:8000/api/vms/user/${selectedUsername}`, { method: 'DELETE', headers: { 'Authorization': `Bearer ${token}` } });
    if (response.ok) { document.getElementById('userDetailPanel').style.display = 'none'; selectedUsername = null; await fetchUsersFromBackend(); renderUserTable(); }
    else { const err = await response.json(); alert("❌ " + (err.detail || "Không thể xóa tài khoản!")); }
  } catch (e) { alert("❌ Không thể kết nối tới máy chủ Backend!"); }
}
// HÀM MỚI: Đồng bộ tên và vai trò người dùng lên thanh Top Header
function syncSessionUserDisplayName() {
    const userStr = localStorage.getItem('currentUser');
    if (userStr) {
        const user = JSON.parse(userStr);
        
        // Tìm thẻ hiển thị tên
        const nameEl = document.getElementById('display-user-name');
        if (nameEl) {
            nameEl.textContent = user.name;
        }

        // Tìm thẻ hiển thị vai trò (nếu có)
        const roleEl = document.getElementById('display-user-role');
        if (roleEl) {
            roleEl.textContent = user.role;
        }

        // Cập nhật Avatar (Lấy 2 chữ cái đầu của tên)
        const avatarEl = document.getElementById('display-user-avatar');
        if (avatarEl) {
            const initials = user.name.split(' ').map(n => n[0]).join('').slice(-2).toUpperCase();
            avatarEl.textContent = initials;
        }
    }
}

// 1. Hàm Bật/Tắt Menu Dropdown
function toggleUserDropdown() {
    const menu = document.getElementById('user-dropdown-menu');
    if (menu) {
        // Nếu đang ẩn thì hiện, đang hiện thì ẩn
        menu.style.display = (menu.style.display === 'none' || menu.style.display === '') ? 'block' : 'none';
    }
}

// 2. Lắng nghe sự kiện click chuột ra ngoài vùng Menu để tự động đóng Menu lại cho mượt mà
document.addEventListener('click', function(event) {
    const userChip = document.querySelector('.user-chip');
    const dropdownMenu = document.getElementById('user-dropdown-menu');
    
    // Nếu click không trúng vào khu vực Avatar/Tên và cũng không trúng vào Menu thì đóng menu lại
    if (dropdownMenu && userChip && !userChip.contains(event.target) && !dropdownMenu.contains(event.target)) {
        dropdownMenu.style.display = 'none';
    }
});

// 3. Hàm Xử lý Đăng xuất
function processLogout() {
    if (confirm("Bạn có chắc chắn muốn đăng xuất khỏi hệ thống?")) {
        // Xóa sạch Token và Thông tin phiên lưu trong trình duyệt
        localStorage.removeItem('currentUser');
        localStorage.removeItem('token');
        
        // Điều hướng thẳng về trang đăng nhập
        window.location.href = 'login.html';
    }
}

// 🌟 15. LUỒNG QUÉT PROXY: Gọi API Backend 8000 thay vì gọi Go2RTC 1984
async function startCameraHealthMonitor() {
  setInterval(async () => {
    try {
      const response = await fetch('http://127.0.0.1:8000/api/vms/cameras');
      if (!response.ok) return;
      const backendCameras = await response.json();
      let hasChanges = false;

      backendCameras.forEach(bCam => {
        const localCam = ALL_CAMERAS.find(c => c.id === bCam.id);
        if (localCam) {
          if (localCam.status !== bCam.status) {
            localCam.status = bCam.status;
            hasChanges = true;
            updateSingleCameraUI(localCam);
          }
        }
      });

      if (hasChanges) {
        if (document.getElementById('counterToolbar')) {
          const onlineCount = ALL_CAMERAS.filter(c => c.status === 'online').length;
          const listToRender = ALL_CAMERAS.slice(0, currentGridLimit);
          document.getElementById('counterToolbar').innerHTML = `Hiển thị ${listToRender.length}/${ALL_CAMERAS.length} camera · <span style="color:var(--green-600);">● ${onlineCount} camera đang hoạt động</span>`;
        }
        if (document.getElementById('statTotalCameras')) renderDashboardStats();
      }
    } catch (error) { console.error("Lỗi vòng quét proxy:", error); }
  }, 1000);
}

function updateSingleCameraUI(cam) {
  const tile = document.querySelector(`.live-tile[data-id="${cam.id}"]`) || 
               document.querySelector(`.cam-tile[data-id="${cam.id}"]`);
  if (!tile) return;

  const tagEl = tile.querySelector('.cam-tag');

  // Nếu là online, gỡ bỏ ngay lập tức và trả lại khung hình
  if (cam.status === 'online') {
    let overlay = tile.querySelector('.cam-status-overlay');
    if (overlay) overlay.remove();

    if (tagEl) tagEl.style.display = '';
    
    let mediaContainer = tile.querySelector('.cam-media-container');
    // Chỉ nạp lại iframe nếu bên trong chưa có iframe/img
    if (mediaContainer && mediaContainer.innerHTML.includes('background')) {
        const mediaHTML = (cam.type === 'video') 
          ? `<iframe src="http://127.0.0.1:1984/stream.html?src=${cam.id}&mode=webrtc" frameborder="0" scrolling="no" style="width:100%; height:100%; pointer-events:none; display:block;"></iframe>` 
          : `<img src="${cam.src}" style="width:100%; height:100%; object-fit:fill; pointer-events:none; display:block;">`;
        mediaContainer.innerHTML = mediaHTML;
    }
    return;
  }

  // Nếu là offline -> gỡ tag Live và phủ màn hình
  if (tagEl) tagEl.style.display = 'none';

  let overlay = tile.querySelector('.cam-status-overlay');
  let mediaContainer = tile.querySelector('.cam-media-container');
  if (mediaContainer) mediaContainer.innerHTML = `<div style="width:100%; height:100%; background:#0f172a;"></div>`;
  
  if (!overlay) {
    const overlayHTML = `<div class="cam-status-overlay" style="position:absolute; top:0; left:0; width:100%; height:100%; background:rgba(15,23,42,0.94); display:flex; flex-direction:column; align-items:center; justify-content:center; color:#f87171; z-index:4; gap:8px; text-align:center; padding:12px; backdrop-filter: blur(1px);"><svg width="24" height="24" viewBox="0 0 24 24" fill="none" stroke="currentColor" stroke-width="2" style="margin-bottom:2px;"><path d="m1 1 22 22M16.74 11.24a6 6 0 0 1 0 8.49M14 14a3 3 0 0 1 0 4.24M8.18 8.18a6 6 0 0 0 0 8.49M10.24 10.24a3 3 0 0 0 0 4.24"/></svg><div style="font-weight:700; font-size:12.5px; letter-spacing:0.5px;">MẤT TÍN HIỆU STREAM</div><div style="font-size:11px; color:#94a3b8; font-weight:400;">Thiết bị ngoại tuyến hoặc sai thông số IP: ${cam.ip}</div></div>`;
    tile.insertAdjacentHTML('beforeend', overlayHTML);
  }
}


function switchSettingsTab(el) {
  document.querySelectorAll('.settings-tab').forEach(t => t.classList.remove('active'));
  el.classList.add('active');
  const tabName = el.getAttribute('data-tab');
  document.querySelectorAll('.settings-panel').forEach(p => {
    p.style.display = (p.getAttribute('data-panel') === tabName) ? 'block' : 'none';
  });
}

async function fetchSettingsFromBackend() {
  try {
    const response = await fetch('http://127.0.0.1:8000/api/vms/settings');
    if (response.ok) return await response.json();
  } catch (e) { console.error("Lỗi tải cài đặt:", e); }
  return null;
}

function fillSettingsForm(settings) {
  if (!settings) return;
  const n = settings.notifications || {};
  document.getElementById('setEmailEnabled').checked = !!n.email_enabled;
  document.getElementById('setEmailAddress').value = n.email_address || '';
  document.getElementById('setSmsEnabled').checked = !!n.sms_enabled;
  document.getElementById('setSmsPhone').value = n.sms_phone || '';
  document.getElementById('setMinAlertLevel').value = n.min_alert_level || 'trung_binh';

  const i = settings.integration || {};
  document.getElementById('setGo2rtcUrl').value = i.go2rtc_api_url || '';
  document.getElementById('setWebhookEnabled').checked = !!i.webhook_enabled;
  document.getElementById('setWebhookUrl').value = i.webhook_url || '';

  const b = settings.backup || {};
  document.getElementById('setAutoBackupEnabled').checked = !!b.auto_backup_enabled;
  document.getElementById('setAutoBackupInterval').value = b.auto_backup_interval_hours || 24;

  const s = settings.security || {};
  document.getElementById('setSessionTimeout').value = s.session_timeout_hours || 8;
  document.getElementById('setMinPasswordLength').value = s.min_password_length || 6;
  document.getElementById('setForcePasswordChangeDays').value = s.force_password_change_days ?? 0;
}

async function checkMediaServerStatus() {
  const dot = document.getElementById('mediaServerStatusDot');
  const txt = document.getElementById('mediaServerStatusText');
  if (!dot || !txt) return;
  try {
    const url = document.getElementById('setGo2rtcUrl').value || 'http://127.0.0.1:1984';
    const response = await fetch(`${url}/api/streams`, { signal: AbortSignal.timeout(2500) });
    if (response.ok) {
      dot.style.background = '#16a34a';
      txt.textContent = 'Media Server (go2rtc) đang hoạt động bình thường';
    } else {
      dot.style.background = '#dc2626';
      txt.textContent = 'Media Server phản hồi lỗi';
    }
  } catch (e) {
    dot.style.background = '#dc2626';
    txt.textContent = 'Không thể kết nối tới Media Server';
  }
}

async function saveNotificationSettings() {
  const payload = {
    email_enabled: document.getElementById('setEmailEnabled').checked,
    email_address: document.getElementById('setEmailAddress').value.trim(),
    sms_enabled: document.getElementById('setSmsEnabled').checked,
    sms_phone: document.getElementById('setSmsPhone').value.trim(),
    min_alert_level: document.getElementById('setMinAlertLevel').value
  };
  await putSettingsSection('notifications', payload);
}

async function saveIntegrationSettings() {
  const payload = {
    go2rtc_api_url: document.getElementById('setGo2rtcUrl').value.trim(),
    webhook_url: document.getElementById('setWebhookUrl').value.trim(),
    webhook_enabled: document.getElementById('setWebhookEnabled').checked
  };
  await putSettingsSection('integration', payload);
  checkMediaServerStatus();
}

async function saveBackupSettings() {
  const payload = {
    auto_backup_enabled: document.getElementById('setAutoBackupEnabled').checked,
    auto_backup_interval_hours: parseInt(document.getElementById('setAutoBackupInterval').value) || 24
  };
  await putSettingsSection('backup', payload);
}

async function saveSecuritySettings() {
  const payload = {
    session_timeout_hours: parseInt(document.getElementById('setSessionTimeout').value) || 8,
    min_password_length: parseInt(document.getElementById('setMinPasswordLength').value) || 6,
    force_password_change_days: parseInt(document.getElementById('setForcePasswordChangeDays').value) || 0
  };
  await putSettingsSection('security', payload);
}

async function putSettingsSection(section, payload) {
  const token = localStorage.getItem('token');
  if (!token) return alert("🔒 Phiên làm việc hết hạn, vui lòng đăng nhập lại!");
  try {
    const response = await fetch(`http://127.0.0.1:8000/api/vms/settings/${section}`, {
      method: 'PUT',
      headers: { 'Content-Type': 'application/json', 'Authorization': `Bearer ${token}` },
      body: JSON.stringify(payload)
    });
    if (response.ok) alert("✅ Đã lưu cài đặt!");
    else { const err = await response.json(); alert("❌ " + (err.detail || "Lỗi khi lưu cài đặt!")); }
  } catch (e) { alert("❌ Không thể kết nối tới máy chủ Backend!"); }
}

async function restartMediaServer() {
  if (!confirm("Khởi động lại Media Server (go2rtc) sẽ tạm gián đoạn toàn bộ luồng camera trong vài giây. Tiếp tục?")) return;
  const token = localStorage.getItem('token');
  try {
    const response = await fetch('http://127.0.0.1:8000/api/vms/system/restart-media', { method: 'POST', headers: { 'Authorization': `Bearer ${token}` } });
    if (response.ok) { alert("✅ Đã gửi lệnh khởi động lại Media Server."); setTimeout(checkMediaServerStatus, 4000); }
    else { const err = await response.json(); alert("❌ " + (err.detail || "Không thể khởi động lại Media Server!")); }
  } catch (e) { alert("❌ Không thể kết nối tới máy chủ Backend!"); }
}

async function exportBackup() {
  const token = localStorage.getItem('token');
  try {
    const response = await fetch('http://127.0.0.1:8000/api/vms/backup/export', { headers: { 'Authorization': `Bearer ${token}` } });
    if (!response.ok) { const err = await response.json(); alert("❌ " + (err.detail || "Không thể xuất bản sao lưu!")); return; }
    const data = await response.json();
    const blob = new Blob([JSON.stringify(data, null, 2)], { type: 'application/json' });
    const url = URL.createObjectURL(blob);
    const a = document.createElement('a');
    a.href = url;
    a.download = `multicamai_backup_${new Date().toISOString().slice(0,19).replace(/[:T]/g,'-')}.json`;
    document.body.appendChild(a); a.click(); document.body.removeChild(a);
    URL.revokeObjectURL(url);
  } catch (e) { alert("❌ Không thể kết nối tới máy chủ Backend!"); }
}

async function restoreBackup(event) {
  const file = event.target.files[0];
  if (!file) return;
  if (!confirm("Phục hồi sẽ GHI ĐÈ toàn bộ camera, người dùng và cài đặt hiện tại. Bạn có chắc chắn?")) { event.target.value = ''; return; }
  const token = localStorage.getItem('token');
  try {
    const text = await file.text();
    const parsed = JSON.parse(text);
    const payload = { cameras: parsed.cameras || null, users: parsed.users || null, settings: parsed.settings || null };
    const response = await fetch('http://127.0.0.1:8000/api/vms/backup/restore', {
      method: 'POST',
      headers: { 'Content-Type': 'application/json', 'Authorization': `Bearer ${token}` },
      body: JSON.stringify(payload)
    });
    if (response.ok) { alert("✅ Đã phục hồi cấu hình thành công! Trang sẽ tải lại."); window.location.reload(); }
    else { const err = await response.json(); alert("❌ " + (err.detail || "Không thể phục hồi bản sao lưu!")); }
  } catch (e) { alert("❌ File sao lưu không hợp lệ hoặc lỗi kết nối!"); }
  event.target.value = '';
}  

document.addEventListener("DOMContentLoaded", async () => {
   if (!checkAuthSecurity()) return;
   syncSessionUserDisplayName();

   await fetchCamerasFromBackend();
   if (document.getElementById('userTableBody')) { await fetchUsersFromBackend(); renderUserTable(); }
   if(document.getElementById('liveCamGrid')) {
     renderLiveGrid();
     handleLiveQueryAction();
   }
   if(document.getElementById('overviewCamGrid')) renderOverviewGrid();
   if(document.getElementById('statTotalCameras')) {
     renderDashboardStats();
     await refreshDashboard();
     if (DASHBOARD_REFRESH_TIMER) clearInterval(DASHBOARD_REFRESH_TIMER);
     DASHBOARD_REFRESH_TIMER = setInterval(refreshDashboard, 10000);
   }
   if(document.getElementById('camManagementTableBody')) renderCamManagementTable();
   if(document.getElementById('settingsPanelHost')) {
     const settings = await fetchSettingsFromBackend();
     fillSettingsForm(settings);
     checkMediaServerStatus();
   }

   // Bật tính năng vượt CORS
   startCameraHealthMonitor();
});
