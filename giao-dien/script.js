// Khai báo mảng Camera (Giữ nguyên như cũ)
const ALL_CAMERAS = [
  { id: "cam_huyen_01", index: 1, name: "Hành lang tầng 2", ip: "10.10.10.12", model: "Hikvision DS-2CD2143G2", zone: "Tòa nhà A / Huyện 1", loc: "Hành lang T2", type: "video", src: "http://127.0.0.1:1984/api/stream.mp4?src=cam_huyen_01", tag: "Live", status: "online" },
  { id: "cam_huyen_02", index: 2, name: "Cổng chính cơ quan", ip: "10.10.10.11", model: "Hikvision DS-2CD1123G0", zone: "Khu ngoại vi / Huyện 2", loc: "Cổng kiểm soát", type: "video", src: "http://127.0.0.1:1984/api/stream.mp4?src=cam_huyen_02", tag: "Live", status: "online" },
];

let currentGridLimit = 6;

// 1. Hàm Login sẽ điều hướng sang trang Tổng quan hoặc Camera
function doLogin() {
  window.location.href = 'index.html'; 
}

// 2. Hàm Render Lưới Camera
function renderLiveGrid() {
  const liveGrid = document.getElementById('liveCamGrid');
  if (!liveGrid) return; // Nếu không phải trang live thì bỏ qua
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
  document.getElementById('counterToolbar').innerHTML = `Hiển thị ${listToRender.length}/${ALL_CAMERAS.length} camera · <span style="color:var(--green-600);">● ${onlineCount} camera đang hoạt động</span>`;
  
  const firstTile = liveGrid.querySelector('.live-tile');
  if (firstTile) selectCam(firstTile);
}

function changeGridLimit(val) {
  currentGridLimit = parseInt(val);
  document.getElementById('liveCamGrid').style.gridTemplateColumns = (currentGridLimit <= 4) ? 'repeat(2, 1fr)' : 'repeat(3, 1fr)';
  renderLiveGrid();
}

function selectCam(el){
  document.querySelectorAll('.live-tile').forEach(t => t.classList.remove('selected'));
  el.classList.add('selected');
  const camId = el.getAttribute('data-id');
  const camData = ALL_CAMERAS.find(c => c.id === camId);
  if (!camData) return;
  
  if(document.getElementById('sideCamName')) document.getElementById('sideCamName').textContent = `${camData.index}. ${camData.name}`;
  if(document.getElementById('sideCamIP')) document.getElementById('sideCamIP').textContent = camData.ip;
  if(document.getElementById('sideCamModel')) document.getElementById('sideCamModel').textContent = camData.model;
  
  const detailContainer = document.getElementById('mainDetailContainer');
  if (detailContainer) {
    if (camData.type === 'video') {
      detailContainer.innerHTML = `<iframe src="http://127.0.0.1:1984/stream.html?src=${camData.id}&mode=webrtc" frameborder="0" scrolling="no" style="width:100%; aspect-ratio:16/9; pointer-events:none; display:block;"></iframe>`;
    } else {
      detailContainer.innerHTML = `<img id="mainDetailThumb" class="cam-thumb" src="${camData.src}" style="width:100%; aspect-ratio:16/9; object-fit:fill;">`;
    }
  }
}

function maximizeCam(el) {
  const mediaElement = el.querySelector('iframe') || el.querySelector('img');
  if (!mediaElement) return;
  if (mediaElement.requestFullscreen) mediaElement.requestFullscreen();
}

// 3. Tự động chạy render khi mở trang live.html
document.addEventListener("DOMContentLoaded", () => {
   if(document.getElementById('liveCamGrid')) {
       renderLiveGrid();
   }
});

// 1. DÁN THÊM HÀM NÀY VÀO CUỐI FILE script.js:
function renderOverviewGrid() {
  const overviewGrid = document.getElementById('overviewCamGrid');
  if (!overviewGrid) return;
  
  overviewGrid.innerHTML = '';
  
  // Chỉ lấy 4 camera đầu tiên để hiển thị ở màn Tổng quan cho nhẹ
  const listToRender = ALL_CAMERAS.slice(0, 4);
  
  listToRender.forEach((cam) => {
    // Nhúng iframe WebRTC giống hệt trang Live
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


// 2. TÌM VÀ SỬA ĐOẠN CUỐI CÙNG NÀY (Để JS nhận diện trang nào thì load lưới trang đó):
document.addEventListener("DOMContentLoaded", () => {
   // Nếu đang mở trang Camera Trực tiếp
   if(document.getElementById('liveCamGrid')) {
       renderLiveGrid();
   }
   // Nếu đang mở trang Tổng quan
   if(document.getElementById('overviewCamGrid')) {
       renderOverviewGrid();
   }
});