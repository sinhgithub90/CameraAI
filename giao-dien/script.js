// script.js
let ALL_CAMERAS = [];
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

// 2. Điều hướng Đăng nhập
function doLogin(){
  window.location.href = 'live.html';
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
      
      // Delay 1 giây để Go2RTC tái khởi động mượt mà, sau đó nạp lại bảng dữ liệu mới
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

// Khởi phát sự kiện nạp tài nguyên đa trang đồng bộ toàn diện
document.addEventListener("DOMContentLoaded", async () => {
   // BẮT BUỘC: Đồng bộ dữ liệu thô từ FastAPI trước khi vẽ bất kỳ trang nào
   await fetchCamerasFromBackend();
   
   if(document.getElementById('liveCamGrid')) renderLiveGrid();
   if(document.getElementById('overviewCamGrid')) renderOverviewGrid();
   if(document.getElementById('camManagementTableBody')) renderCamManagementTable();
});