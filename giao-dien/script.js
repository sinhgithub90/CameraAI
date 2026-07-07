// script.js
let ALL_CAMERAS = [];
let ALL_USERS = [];
let selectedUsername = null;
let currentGridLimit = 6;

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
    const tileHTML = `<div class="live-tile ${isSelected}" data-id="${cam.id}" onclick="selectCam(this)" ondblclick="maximizeCam(this)" style="position:relative; cursor:pointer;"><div class="cam-media-container" style="width:100%; height:100%; pointer-events:none;">${mediaHTML}</div>${statusOverlayHTML}<div class="cam-tag" style="${cam.tag === 'Live' ? 'background:#dc2626' : ''}; z-index:10; pointer-events:none;">${cam.tag}</div><div class="cam-label" style="z-index:10; pointer-events:none;">● ${cam.index}. ${cam.name}</div></div>`;
    // <div class="cam-time" style="z-index:10; pointer-events:none;">${new Date().toLocaleTimeString()}</div>
    liveGrid.insertAdjacentHTML('beforeend', tileHTML);
  });
  const onlineCount = ALL_CAMERAS.filter(c => c.status === 'online').length;
  if(document.getElementById('counterToolbar')) document.getElementById('counterToolbar').innerHTML = `Hiển thị ${listToRender.length}/${ALL_CAMERAS.length} camera · <span style="color:var(--green-600);">● ${onlineCount} camera đang hoạt động</span>`;
  const firstTile = liveGrid.querySelector('.live-tile');
  if (firstTile) selectCam(firstTile);
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
    const trHTML = `<tr><td><b>#${cam.index}</b></td><td><span style="font-weight:600; color:var(--slate-900);">${cam.name}</span></td><td><span class="pill thap" style="font-family:monospace;">${cam.ip}</span></td><td>${cam.model}</td><td>${cam.zone}</td><td>${cam.loc}</td><td><span class="status daxuly"><span class="d"></span>${cam.status === 'online' ? 'Hoạt động' : 'Ngoại tuyến'}</span></td><td><button class="btn-sm red" onclick="deleteCamera('${cam.id}')" style="width:auto; padding:5px 12px; display:inline-block; cursor:pointer;">Xóa</button></td></tr>`;
    tbody.insertAdjacentHTML('beforeend', trHTML);
  });
}

async function handleAddCamera(event) {
  event.preventDefault();
  const name = document.getElementById('modalCamName').value;
  const ip = document.getElementById('modalCamIP').value;
  const model = document.getElementById('modalCamModel').value;
  const user = document.getElementById('modalCamUser').value;
  const pass = document.getElementById('modalCamPass').value;
  const zone = document.getElementById('modalCamZone').value;
  const loc = document.getElementById('modalCamLoc').value;
  const submitBtn = event.target.querySelector('button[type="submit"]');
  const originalText = submitBtn.textContent;
  submitBtn.textContent = "Đang kết nối & khởi động lại..."; submitBtn.disabled = true;
  try {
    const response = await fetch('http://127.0.0.1:8000/api/vms/camera/add', { method: 'POST', headers: { 'Content-Type': 'application/json' }, body: JSON.stringify({ name, ip, user, password: pass, model, zone, loc }) });
    if (response.ok) { closeAddCamModal(); document.getElementById('addCamForm').reset(); setTimeout(async () => { await fetchCamerasFromBackend(); renderCamManagementTable(); }, 1000); } 
    else alert("Lỗi ghi nhận cấu hình từ Backend.");
  } catch (error) { alert("Không thể kết nối API tới FastAPI."); } finally { submitBtn.textContent = originalText; submitBtn.disabled = false; }
}

async function deleteCamera(id) {
  if (confirm('Bạn có chắc chắn muốn xóa camera này ra khỏi hệ thống?')) {
    try {
      const response = await fetch(`http://127.0.0.1:8000/api/vms/camera/${id}`, { method: 'DELETE' });
      if (response.ok) { await fetchCamerasFromBackend(); renderCamManagementTable(); }
    } catch (error) { alert("Lỗi khi kết nối yêu cầu xóa."); }
  }
}

function openAddCamModal() { const modal = document.getElementById('addCamModal'); if (modal) modal.style.display = 'flex'; }
function closeAddCamModal() { const modal = document.getElementById('addCamModal'); if (modal) modal.style.display = 'none'; }
function changeGridLimit(val) { currentGridLimit = parseInt(val); const liveGrid = document.getElementById('liveCamGrid'); if(liveGrid) { liveGrid.style.gridTemplateColumns = (currentGridLimit === 2 || currentGridLimit === 4) ? 'repeat(2, 1fr)' : 'repeat(3, 1fr)'; renderLiveGrid(); } }
function selectCam(el){ document.querySelectorAll('.live-tile').forEach(t => t.classList.remove('selected')); el.classList.add('selected'); const camId = el.getAttribute('data-id'); const camData = ALL_CAMERAS.find(c => c.id === camId); if (!camData) return; if(document.getElementById('sideCamName')) document.getElementById('sideCamName').textContent = `${camData.index}. ${camData.name}`; if(document.getElementById('sideCamIP')) document.getElementById('sideCamIP').textContent = camData.ip; if(document.getElementById('sideCamModel')) document.getElementById('sideCamModel').textContent = camData.model; if(document.getElementById('sideCamZone')) document.getElementById('sideCamZone').textContent = camData.zone; if(document.getElementById('sideCamLoc')) document.getElementById('sideCamLoc').textContent = camData.loc; const detailContainer = document.getElementById('mainDetailContainer'); if (detailContainer) { detailContainer.innerHTML = `<iframe src="http://127.0.0.1:1984/stream.html?src=${camData.id}&mode=webrtc" frameborder="0" scrolling="no" style="width:100%; aspect-ratio:16/9; pointer-events:none; display:block;"></iframe>`; } }
function maximizeCam(el) { const mediaElement = el.querySelector('iframe') || el.querySelector('img'); if (mediaElement && mediaElement.requestFullscreen) mediaElement.requestFullscreen(); }

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
  const permissionsList = ['live', 'playback', 'cammgmt', 'usermgmt', 'alertmgmt', 'reports', 'sysconfig', 'export'];
  const activePermissions = permissionsList.filter(p => { const chk = document.getElementById(`chk_${p}`); return chk ? chk.checked : false; });
  const payload = { name: document.getElementById('inpDetailName').value.trim(), email: document.getElementById('inpDetailEmail').value.trim(), phone: document.getElementById('inpDetailPhone').value.trim(), unit: document.getElementById('inpDetailUnit').value.trim(), role: document.getElementById('selDetailRole').value, permissions: activePermissions };
  try {
    const response = await fetch(`http://127.0.0.1:8000/api/vms/user/${selectedUsername}`, { method: 'PUT', headers: { 'Content-Type': 'application/json', 'Authorization': `Bearer ${token}` }, body: JSON.stringify(payload) });
    if (response.ok) await fetchUsersFromBackend(); 
  } catch (e) { console.error("Lỗi kết nối lưu quyền tự động:", e); }
}

function openAddUserModal() { document.getElementById('addUserModal').style.display = 'flex'; }
function closeAddUserModal() { document.getElementById('addUserModal').style.display = 'none'; }

async function resetUserPassword() {
  if (!selectedUsername) return;
  const token = localStorage.getItem('token');
  try {
    const response = await fetch(`http://127.0.0.1:8000/api/vms/user/${selectedUsername}/reset-password`, { method: 'POST', headers: { 'Authorization': `Bearer ${token}` } });
    if (response.ok) { const result = await response.json(); alert(`Đã đặt lại mật khẩu thành công!\\nMật khẩu mới: ${result.new_password}`); }
  } catch (e) {}
}

async function toggleLockAccount() {
  if (!selectedUsername) return;
  const token = localStorage.getItem('token');
  try {
    const response = await fetch(`http://127.0.0.1:8000/api/vms/user/${selectedUsername}/toggle-lock`, { method: 'POST', headers: { 'Authorization': `Bearer ${token}` } });
    if (response.ok) { await fetchUsersFromBackend(); selectUserAccount(selectedUsername); }
  } catch (e) {}
}

async function deleteUserAccount() {
  if (!selectedUsername) return;
  if (!confirm(`Xóa vĩnh viễn tài khoản @${selectedUsername}?`)) return;
  const token = localStorage.getItem('token');
  try {
    const response = await fetch(`http://127.0.0.1:8000/api/vms/user/${selectedUsername}`, { method: 'DELETE', headers: { 'Authorization': `Bearer ${token}` } });
    if (response.ok) { document.getElementById('userDetailPanel').style.display = 'none'; selectedUsername = null; await fetchUsersFromBackend(); renderUserTable(); }
  } catch (e) {}
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

      if (hasChanges && document.getElementById('counterToolbar')) {
        const onlineCount = ALL_CAMERAS.filter(c => c.status === 'online').length;
        const listToRender = ALL_CAMERAS.slice(0, currentGridLimit);
        document.getElementById('counterToolbar').innerHTML = `Hiển thị ${listToRender.length}/${ALL_CAMERAS.length} camera · <span style="color:var(--green-600);">● ${onlineCount} camera đang hoạt động</span>`;
      }
    } catch (error) { console.error("Lỗi vòng quét proxy:", error); }
  }, 3000);
}

function updateSingleCameraUI(cam) {
  const tile = document.querySelector(`.live-tile[data-id="${cam.id}"]`) || 
               document.querySelector(`.cam-tile[data-id="${cam.id}"]`);
  if (!tile) return;

  // Nếu là online, gỡ bỏ ngay lập tức và trả lại khung hình
  if (cam.status === 'online') {
    let overlay = tile.querySelector('.cam-status-overlay');
    if (overlay) overlay.remove();
    
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

  // Nếu là offline -> mới thực hiện phủ màn hình
  let overlay = tile.querySelector('.cam-status-overlay');
  let mediaContainer = tile.querySelector('.cam-media-container');
  if (mediaContainer) mediaContainer.innerHTML = `<div style="width:100%; height:100%; background:#0f172a;"></div>`;
  
  if (!overlay) {
    const overlayHTML = `<div class="cam-status-overlay" ... > MẤT TÍN HIỆU </div>`; // (Giữ nguyên html overlay của bạn)
    tile.insertAdjacentHTML('beforeend', overlayHTML);
  }
}


document.addEventListener("DOMContentLoaded", async () => {
   if (!checkAuthSecurity()) return;
   
   // Gọi hàm đồng bộ giao diện người dùng
   syncSessionUserDisplayName(); 

   await fetchCamerasFromBackend();
   if (document.getElementById('userTableBody')) { await fetchUsersFromBackend(); renderUserTable(); }
   if(document.getElementById('liveCamGrid')) renderLiveGrid();
   if(document.getElementById('overviewCamGrid')) renderOverviewGrid();
   if(document.getElementById('camManagementTableBody')) renderCamManagementTable();

   // Bật tính năng vượt CORS
   startCameraHealthMonitor();
});