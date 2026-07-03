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
      console.log("👉 Đã tải danh sách camera từ Backend thành công:", ALL_CAMERAS);
    } else {
      console.error("Lỗi phản hồi dữ liệu từ Backend:", response.status);
    }
  } catch (error) {
    console.error("❌ Không thể kết nối tới FastAPI (Port 8000). Đảm bảo bạn đã chạy python main.py!", error);
  }
}

// 2. Hàm gọi API lấy danh sách người dùng từ Backend FastAPI
async function fetchUsersFromBackend() {
  try {
    const response = await fetch('http://127.0.0.1:8000/api/vms/users');
    if (response.ok) {
      ALL_USERS = await response.json();
      console.log("👉 Đã tải danh sách tài khoản từ Backend thành công:", ALL_USERS);
    } else {
      console.error("Lỗi lấy danh sách tài khoản từ Backend:", response.status);
    }
  } catch (e) {
    console.error("❌ Không thể kết nối API người dùng tới FastAPI:", e);
  }
}

// Hàm đăng nhập form truyền thống
async function doLogin() {
  const userInp = document.getElementById('loginUser').value.trim();
  const passInp = document.getElementById('loginPass').value.trim();

  if(!userInp || !passInp) {
    alert("Vui lòng điền đầy đủ tài khoản và mật khẩu!");
    return;
  }

  try {
    const response = await fetch(`http://127.0.0.1:8000/api/auth/token?user=${encodeURIComponent(userInp)}&text_pass=${encodeURIComponent(passInp)}`, {
      method: 'POST'
    });

    if (response.ok) {
      const data = await response.json();
      localStorage.setItem('currentUser', JSON.stringify(data.user));
      window.location.href = 'index.html';
    } else {
      const err = await response.json();
      alert(err.detail || "Đăng nhập thất bại!");
    }
  } catch (error) {
    alert("Không thể kết nối đến hệ thống Backend FastAPI!");
  }
}

// 👁️ Chức năng ẩn/hiện mật khẩu khi click biểu tượng con mắt
function togglePasswordVisibility() {
  const passInput = document.getElementById('loginPass');
  const eyeIcon = document.querySelector('.toggle-eye');
  if (passInput.type === 'password') {
    passInput.type = 'text';
    eyeIcon.style.color = 'var(--blue-600)';
  } else {
    passInput.type = 'password';
    eyeIcon.style.color = 'var(--slate-400)';
  }
}

// 🌐 Đăng nhập bằng tài khoản Google mô phỏng
function loginWithGoogleDemo() {
  const emailInput = prompt("MÔ PHỎNG ĐĂNG NHẬP GOOGLE:\nVui lòng nhập địa chỉ Email Google của bạn để xác thực:");
  if (!emailInput) return;

  fetch('http://127.0.0.1:8000/api/vms/users')
    .then(res => res.json())
    .then(users => {
      const foundUser = users.find(u => u.email.toLowerCase() === emailInput.trim().toLowerCase());
      if (foundUser) {
        if (foundUser.status !== "Hoạt động") {
          alert("Tài khoản liên kết với Email này hiện đang bị tạm khóa!");
          return;
        }
        localStorage.setItem('currentUser', JSON.stringify({
          username: foundUser.username,
          name: foundUser.name,
          role: foundUser.role,
          unit: foundUser.unit,
          permissions: foundUser.permissions
        }));
        alert(`Xác thực Google thành công! Xin chào ${foundUser.name}.`);
        window.location.href = 'index.html';
      } else {
        alert("🔒 Đăng nhập thất bại: Email Google này chưa được quy hoạch đăng ký trong hệ thống!");
      }
    })
    .catch(() => alert("Không thể kết nối đến Backend!"));
}

// Hàm bảo vệ định tuyến an ninh hệ thống đa trang
function checkAuthSecurity() {
  const currentUser = JSON.parse(localStorage.getItem('currentUser'));
  
  if (!currentUser) {
    if (!window.location.href.includes('login.html')) {
      window.location.href = 'login.html';
    }
    return false;
  }

  const userMenuLink = document.querySelector('a[href="users.html"]');
  if (userMenuLink && currentUser.role !== 'Quản trị viên') {
    userMenuLink.style.display = 'none'; 
  }

  if (window.location.href.includes('users.html') && currentUser.role !== 'Quản trị viên') {
    alert("TỪ CHỐI TRUY CẬP: Chỉ có Quản trị viên tối cao mới có quyền phân quyền!");
    window.location.href = 'live.html';
    return false;
  }
  return true;
}

// 3. Render lưới camera trực tiếp (live.html)
function renderLiveGrid() {
  const liveGrid = document.getElementById('liveCamGrid');
  if (!liveGrid) return;
  liveGrid.innerHTML = '';
  
  const listToRender = ALL_CAMERAS.slice(0, currentGridLimit);
  
  listToRender.forEach((cam, i) => {
    const isSelected = i === 0 ? 'selected' : '';
    const mediaHTML = cam.type === 'video' 
      ? `<iframe src="http://127.0.0.1:1984/stream.html?src=${cam.id}&mode=webrtc" frameborder="0" scrolling="no" style="width:100%; height:100%; pointer-events:none; display:block;"></iframe>` 
      : `<img src="${cam.src}" style="width:100%; height:100%; object-fit:fill; pointer-events:none; display:block;">`;
      
    const tileHTML = `
      <div class="live-tile ${isSelected}" data-id="${cam.id}" onclick="selectCam(this)" ondblclick="maximizeCam(this)" style="position:relative; cursor:pointer;">
        <div style="width:100%; height:100%; pointer-events:none;">
          ${mediaHTML}
        </div>
        <div class="cam-tag" style="${cam.tag === 'Live' ? 'background:#dc2626' : ''}; z-index:10; pointer-events:none;">${cam.tag}</div>
        <div class="cam-label" style="z-index:10; pointer-events:none;">● ${cam.index}. ${cam.name}</div>
        <div class="cam-time" style="z-index:10; pointer-events:none;">${new Date().toLocaleTimeString()}</div>
      </div>
    `;
    liveGrid.insertAdjacentHTML('beforeend', tileHTML);
  });
  
  const onlineCount = ALL_CAMERAS.filter(c => c.status === 'online').length;
  if(document.getElementById('counterToolbar')){
    document.getElementById('counterToolbar').innerHTML = `
      Hiển thị ${listToRender.length}/${ALL_CAMERAS.length} camera · <span style="color:var(--green-600);">● ${onlineCount} camera đang hoạt động</span>
    `;
  }
  
  const firstTile = liveGrid.querySelector('.live-tile');
  if (firstTile) selectCam(firstTile);
}

// 4. Render lưới xem nhanh trực tiếp (index.html)
function renderOverviewGrid() {
  const overviewGrid = document.getElementById('overviewCamGrid');
  if (!overviewGrid) return;
  overviewGrid.innerHTML = '';
  
  const listToRender = ALL_CAMERAS.slice(0, 4);
  listToRender.forEach((cam) => {
    const mediaHTML = cam.type === 'video' 
      ? `<iframe src="http://127.0.0.1:1984/stream.html?src=${cam.id}&mode=webrtc" frameborder="0" scrolling="no" style="width:100%; height:100%; pointer-events:none; display:block;"></iframe>` 
      : `<img src="${cam.src}" style="width:100%; height:100%; object-fit:fill; pointer-events:none; display:block;">`;
      
    const tileHTML = `
      <div class="cam-tile" ondblclick="maximizeCam(this)" style="position:relative; cursor:pointer;">
        <div style="width:100%; height:100%; pointer-events:none;">
          ${mediaHTML}
        </div>
        <div class="cam-label" style="z-index:10; pointer-events:none;">● ${cam.index}. ${cam.name}</div>
        <div class="cam-time" style="z-index:10; pointer-events:none;">${new Date().toLocaleTimeString()}</div>
      </div>
    `;
    overviewGrid.insertAdjacentHTML('beforeend', tileHTML);
  });
}

// 5. Render bảng cấu hình quản lý (cammgmt.html)
function renderCamManagementTable() {
  const tbody = document.getElementById('camManagementTableBody');
  if (!tbody) return;
  tbody.innerHTML = '';
  
  ALL_CAMERAS.forEach((cam) => {
    const trHTML = `
      <tr>
        <td><b>#${cam.index}</b></td>
        <td><span style="font-weight:600; color:var(--slate-900);">${cam.name}</span></td>
        <td><span class="pill thap" style="font-family:monospace;">${cam.ip}</span></td>
        <td>${cam.model}</td>
        <td>${cam.zone}</td>
        <td>${cam.loc}</td>
        <td><span class="status daxuly"><span class="d"></span>${cam.status === 'online' ? 'Hoạt động' : 'Ngoại tuyến'}</span></td>
        <td>
          <button class="btn-sm red" onclick="deleteCamera('${cam.id}')" style="width:auto; padding:5px 12px; display:inline-block; cursor:pointer;">Xóa</button>
        </td>
      </tr>
    `;
    tbody.insertAdjacentHTML('beforeend', trHTML);
  });
}

// 6. Xử lý Thêm Camera gửi dữ liệu lên Backend
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
  submitBtn.textContent = "Đang kết nối & khởi động lại dịch vụ...";
  submitBtn.disabled = true;

  try {
    const response = await fetch('http://127.0.0.1:8000/api/vms/camera/add', {
      method: 'POST',
      headers: { 'Content-Type': 'application/json' },
      body: JSON.stringify({ name, ip, user, password: pass, model, zone, loc })
    });

    if (response.ok) {
      closeAddCamModal();
      document.getElementById('addCamForm').reset();
      
      setTimeout(async () => {
         await fetchCamerasFromBackend();
         renderCamManagementTable();
      }, 1000);
    } else {
      alert("Lỗi ghi nhận cấu hình từ máy chủ Backend.");
    }
  } catch (error) {
    alert("Không thể kết nối API tới FastAPI.");
  } finally {
    submitBtn.textContent = originalText;
    submitBtn.disabled = false;
  }
}

// 7. Xử lý Xóa Camera khỏi hệ thống
async function deleteCamera(id) {
  if (confirm('Bạn có chắc chắn muốn xóa camera này ra khỏi hệ thống quản lý không?')) {
    try {
      const response = await fetch(`http://127.0.0.1:8000/api/vms/camera/${id}`, {
        method: 'DELETE'
      });
      if (response.ok) {
        await fetchCamerasFromBackend();
        renderCamManagementTable();
      }
    } catch (error) {
      alert("Lỗi khi kết nối yêu cầu xóa camera.");
    }
  }
}

// --- CÁC HÀM PHỤ TRỢ TƯƠNG TÁC GIAO DIỆN (GIỮ NGUYÊN) ---
function openAddCamModal() { const modal = document.getElementById('addCamModal'); if (modal) modal.style.display = 'flex'; }
function closeAddCamModal() { const modal = document.getElementById('addCamModal'); if (modal) modal.style.display = 'none'; }
function changeGridLimit(val) { currentGridLimit = parseInt(val); const liveGrid = document.getElementById('liveCamGrid'); if(liveGrid) { liveGrid.style.gridTemplateColumns = (currentGridLimit === 2 || currentGridLimit === 4) ? 'repeat(2, 1fr)' : 'repeat(3, 1fr)'; renderLiveGrid(); } }
function selectCam(el){ document.querySelectorAll('.live-tile').forEach(t => t.classList.remove('selected')); el.classList.add('selected'); const camId = el.getAttribute('data-id'); const camData = ALL_CAMERAS.find(c => c.id === camId); if (!camData) return; if(document.getElementById('sideCamName')) document.getElementById('sideCamName').textContent = `${camData.index}. ${camData.name}`; if(document.getElementById('sideCamIP')) document.getElementById('sideCamIP').textContent = camData.ip; if(document.getElementById('sideCamModel')) document.getElementById('sideCamModel').textContent = camData.model; if(document.getElementById('sideCamZone')) document.getElementById('sideCamZone').textContent = camData.zone; if(document.getElementById('sideCamLoc')) document.getElementById('sideCamLoc').textContent = camData.loc; const detailContainer = document.getElementById('mainDetailContainer'); if (detailContainer) { detailContainer.innerHTML = `<iframe src="http://127.0.0.1:1984/stream.html?src=${camData.id}&mode=webrtc" frameborder="0" scrolling="no" style="width:100%; aspect-ratio:16/9; pointer-events:none; display:block;"></iframe>`; } }
function maximizeCam(el) { const mediaElement = el.querySelector('iframe') || el.querySelector('img'); if (mediaElement && mediaElement.requestFullscreen) mediaElement.requestFullscreen(); }
function selectAlert(el){ document.querySelectorAll('.alert-row').forEach(t=>t.classList.remove('selected')); el.classList.add('selected'); }

// 8. Hàm nạp danh sách người dùng đổ lên bảng dữ liệu trái
function renderUserTable() {
  const userBody = document.getElementById('userTableBody');
  if (!userBody) return;
  userBody.innerHTML = '';

  ALL_USERS.forEach((user) => {
    const initials = user.name.split(' ').map(n => n[0]).join('').slice(-2).toUpperCase();
    const roleClass = user.role === 'Quản trị viên' ? 'admin' : (user.role === 'Giám sát' ? 'giamsat' : 'nhanvien');
    const statusClass = user.status === 'Hoạt động' ? 'daxuly' : 'dangxuly';
    const isRowSelected = user.username === selectedUsername ? 'style="background:var(--blue-50);"' : '';

    const rowHTML = `
      <tr ${isRowSelected} onclick="selectUserAccount('${user.username}')" style="cursor:pointer;">
        <td style="display:flex;align-items:center;gap:8px;">
          <div class="user-row-avatar" style="background:linear-gradient(135deg,#3b82f6,#8b5cf6);color:#fff;font-weight:700;display:flex;align-items:center;justify-content:center;width:34px;height:34px;border-radius:50%;">${initials}</div>
          <div><strong>${user.name}</strong><br><span style="color:var(--slate-400);font-size:11px;">${user.username}</span></div>
        </td>
        <td><span class="role-pill ${roleClass}">${user.role}</span></td>
        <td>${user.unit}</td>
        <td>${user.email}</td>
        <td><span class="status ${statusClass}"><span class="d"></span>${user.status}</span></td>
      </tr>
    `;
    userBody.insertAdjacentHTML('beforeend', rowHTML);
  });
}

// 9. Click chọn xem thông tin & phân quyền chi tiết của 1 tài khoản
function selectUserAccount(username) {
  selectedUsername = username;
  const user = ALL_USERS.find(u => u.username === username);
  if (!user) return;

  renderUserTable(); 

  document.getElementById('userDetailPanel').style.display = 'block';
  document.getElementById('txtDetailName').textContent = user.name;
  document.getElementById('txtDetailUsername').textContent = `@${user.username}`;
  document.getElementById('txtDetailRole').textContent = user.role;
  document.getElementById('txtDetailUnit').textContent = user.unit;
  document.getElementById('detailAvatar').textContent = user.name.split(' ').map(n => n[0]).join('').slice(-2).toUpperCase();

  const statusLbl = document.getElementById('lblDetailStatus');
  statusLbl.textContent = user.status;
  if(user.status === 'Hoạt động') {
    statusLbl.style.background = '#dcfce7'; statusLbl.style.color = '#16a34a';
    document.getElementById('btnLockAccount').textContent = "🔒 Khóa tài khoản";
  } else {
    statusLbl.style.background = '#fee2e2'; statusLbl.style.color = '#dc2626';
    document.getElementById('btnLockAccount').textContent = "🔓 Mở khóa tài khoản";
  }

  document.getElementById('inpDetailName').value = user.name;
  document.getElementById('inpDetailEmail').value = user.email;
  document.getElementById('inpDetailPhone').value = user.phone;
  document.getElementById('inpDetailUnit').value = user.unit;
  document.getElementById('selDetailRole').value = user.role;

  const permissionsList = ['live', 'playback', 'cammgmt', 'usermgmt', 'alertmgmt', 'reports', 'sysconfig', 'export'];
  permissionsList.forEach(p => {
      const chk = document.getElementById(`chk_${p}`);
      if(chk) chk.checked = user.permissions.includes(p);
  });
}

function openAddUserModal() { document.getElementById('addUserModal').style.display = 'flex'; }
function closeAddUserModal() { document.getElementById('addUserModal').style.display = 'none'; }

// 10. Submit tạo tài khoản mới gửi về FastAPI
async function submitNewUser(event) {
  event.preventDefault();
  const payload = {
    username: document.getElementById('modalUserUsername').value.trim(),
    password: document.getElementById('modalUserPassword').value.trim(),
    name: document.getElementById('modalUserName').value.trim(),
    role: document.getElementById('modalUserRole').value,
    unit: document.getElementById('modalUserUnit').value.trim(),
    email: document.getElementById('modalUserEmail').value.trim(),
    phone: document.getElementById('modalUserPhone').value.trim()
  };

  try {
    const response = await fetch('http://127.0.0.1:8000/api/vms/user/add', {
      method: 'POST',
      headers: { 'Content-Type': 'application/json' },
      body: JSON.stringify(payload)
    });
    if (response.ok) {
      closeAddUserModal();
      document.getElementById('addUserForm').reset();
      await fetchUsersFromBackend();
      renderUserTable();
      alert("Đã quy hoạch cấp tài khoản thành công!");
    } else {
      const err = await response.json();
      alert(err.detail || "Lỗi tạo tài khoản mới!");
    }
  } catch (e) { alert("Không thể liên kết tới máy chủ Backend!"); }
}

// 11. Nút LƯU THAY ĐỔI: Cập nhật lý lịch hồ sơ và cấu hình phân quyền
async function saveUserProfileChanges() {
  if (!selectedUsername) return;

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
      headers: { 'Content-Type': 'application/json' },
      body: JSON.stringify(payload)
    });
    if (response.ok) {
      alert("Đã lưu thông tin hồ sơ và cập nhật phân quyền hệ thống!");
      await fetchUsersFromBackend();
      selectUserAccount(selectedUsername);
    }
  } catch (e) { alert("Lỗi kết nối máy chủ không thể lưu thay đổi!"); }
}

// 12. Nút ĐẶT LẠI MẬT KHẨU
async function resetUserPassword() {
  if (!selectedUsername) return;
  try {
    const response = await fetch(`http://127.0.0.1:8000/api/vms/user/${selectedUsername}/reset-password`, { method: 'POST' });
    if (response.ok) {
      const result = await response.json();
      alert(`Đã đặt lại mật khẩu thành công!\nMật khẩu mới của tài khoản @${selectedUsername} là: ${result.new_password}`);
    }
  } catch (e) { alert("Lỗi xử lý yêu cầu đặt lại mật khẩu!"); }
}

// 13. Nút KHÓA / MỞ KHÓA TÀI KHOẢN
async function toggleLockAccount() {
  if (!selectedUsername) return;
  try {
    const response = await fetch(`http://127.0.0.1:8000/api/vms/user/${selectedUsername}/toggle-lock`, { method: 'POST' });
    if (response.ok) {
      const result = await response.json();
      alert(`Trạng thái tài khoản đổi thành: [${result.new_status}]`);
      await fetchUsersFromBackend();
      selectUserAccount(selectedUsername);
    } else {
      const err = await response.json();
      alert(err.detail || "Không thể thao tác trên tài khoản này!");
    }
  } catch (e) { alert("Lỗi khi kết nối thay đổi trạng thái khóa!"); }
}

// 14. Nút XÓA BỎ TÀI KHỎI HỆ THỐNG
async function deleteUserAccount() {
  if (!selectedUsername) return;
  if (!confirm(`Cảnh báo hệ thống: Bạn chắc chắn có muốn xóa vĩnh viễn tài khoản @${selectedUsername} không?`)) return;

  try {
    const response = await fetch(`http://127.0.0.1:8000/api/vms/user/${selectedUsername}`, { method: 'DELETE' });
    if (response.ok) {
      alert("Đã loại bỏ tài khoản người dùng khỏi hệ thống thành công.");
      document.getElementById('userDetailPanel').style.display = 'none';
      selectedUsername = null;
      await fetchUsersFromBackend();
      renderUserTable();
    } else {
      const err = await response.json();
      alert(err.detail || "Không được phép xóa tài khoản này!");
    }
  } catch (e) { alert("Gặp lỗi trong quá trình thực hiện lệnh xóa!"); }
}

// Kích hoạt nạp tài nguyên đa trang đồng bộ toàn diện khi tải trang
document.addEventListener("DOMContentLoaded", async () => {
   // Kiểm tra bảo mật đầu tiên
   if (!checkAuthSecurity()) return;

   await fetchCamerasFromBackend();
   
   if (document.getElementById('userTableBody')) {
       await fetchUsersFromBackend();
       renderUserTable();
   }
   if(document.getElementById('liveCamGrid')) renderLiveGrid();
   if(document.getElementById('overviewCamGrid')) renderOverviewGrid();
   if(document.getElementById('camManagementTableBody')) renderCamManagementTable();
});