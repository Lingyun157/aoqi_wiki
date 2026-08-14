const { createApp, ref, computed, onMounted, onUnmounted, watch, nextTick } = Vue;

const API = '/api';
const TOKEN_KEY = 'aoqi_forum_token';
const USER_KEY = 'aoqi_forum_user';

function getToken() {
    return localStorage.getItem(TOKEN_KEY) || '';
}

function setToken(token) {
    localStorage.setItem(TOKEN_KEY, token);
}

function clearToken() {
    localStorage.removeItem(TOKEN_KEY);
}

function getStoredUser() {
    try {
        return JSON.parse(localStorage.getItem(USER_KEY) || 'null');
    } catch {
        return null;
    }
}

function setStoredUser(user) {
    localStorage.setItem(USER_KEY, JSON.stringify(user));
}

function clearStoredUser() {
    localStorage.removeItem(USER_KEY);
}

function isAdmin(user) {
    if (!user) return false;
    return user.role === 'admin' || user.role === 'super_admin';
}

function isSuperAdmin(user) {
    if (!user) return false;
    return user.role === 'super_admin';
}

function canManageContent(item, currentUser) {
    if (!currentUser) return false;
    if (isAdmin(currentUser)) return true;
    const ownerId = item.userId || item.user_id;
    return ownerId && currentUser.id === ownerId;
}

function debounce(fn, delay) {
    let timer = null;
    return function(...args) {
        if (timer) clearTimeout(timer);
        timer = setTimeout(() => fn(...args), delay);
    };
}

// 模块级回调：当 401 发生时通知 setup 层清除 currentUser（api 函数无法直接访问 setup 内的 ref）
let onAuthFailed = null;

async function api(path, opts) {
    opts = opts || {};
    if (opts.body && !opts.headers && !(opts.body instanceof FormData)) {
        opts.headers = { 'Content-Type': 'application/json' };
    }
    const token = getToken();
    if (token) {
        opts.headers = opts.headers || {};
        opts.headers['Authorization'] = 'Bearer ' + token;
    }
    const res = await fetch(API + path, opts);
    if (res.status === 401) {
        // 登录/注册端点的 401 是业务响应（密码错误等），不应清除 token 或触发登出
        const isAuthEndpoint = path === '/auth/login' || path === '/auth/register';
        if (!isAuthEndpoint) {
            clearToken();
            clearStoredUser();
            // 通知 setup 层更新 UI 状态，否则 UI 仍显示"已登录"但所有请求失败
            if (onAuthFailed) onAuthFailed();
            const err = new Error('登录已过期，请重新登录');
            err.status = 401;
            throw err;
        }
    }
    return res.json();
}

async function uploadImage(file) {
    const formData = new FormData();
    formData.append('file', file);
    return api('/upload/image', {
        method: 'POST',
        body: formData
    });
}

const app = createApp({
    setup() {
        // Nav
        const currentPage = ref('pets');
        const sidebarCollapsed = ref(false);
        const navItems = [
            { id: 'pets', label: '精灵查询', icon: '<circle cx="10" cy="10" r="7" stroke="currentColor" stroke-width="1.5"/><line x1="10" y1="7" x2="10" y2="10" stroke="currentColor" stroke-width="1.5"/><line x1="10" y1="13" x2="10.01" y2="13" stroke="currentColor" stroke-width="2"/>' },

            { id: 'evolution', label: '进化专区', icon: '<path d="M12 2L2 7l10 5 10-5-10-5z" stroke="currentColor" stroke-width="1.5" stroke-linejoin="round"/><path d="M2 17l10 5 10-5" stroke="currentColor" stroke-width="1.5"/><path d="M2 12l10 5 10-5" stroke="currentColor" stroke-width="1.5"/>' },
            { id: 'challenges', label: '挑战活动', icon: '<path d="M10 2L12.5 7.5L18 8.5L14 12.5L15 18L10 15.5L5 18L6 12.5L2 8.5L7.5 7.5L10 2Z" stroke="currentColor" stroke-width="1.5" stroke-linejoin="round"/>' },
            { id: 'videos', label: '攻略专区', icon: '<rect x="3" y="5" width="14" height="10" rx="1.5" stroke="currentColor" stroke-width="1.5"/><path d="M10 8.5L13.5 10.5L10 12.5V8.5Z" fill="currentColor"/>' },
            { id: 'forum', label: '讨论专区', icon: '<path d="M4 5h12v8H8l-4 3V5z" stroke="currentColor" stroke-width="1.5" stroke-linejoin="round"/>' },
        ];

        // Auth
        const currentUser = ref(getStoredUser());
        const showAuthModal = ref(false);
        const authMode = ref('login');
        const authUsername = ref('');
        const authPassword = ref('');
        const authLoading = ref(false);

        // 注册 401 回调：token 过期时清除 UI 登录状态
        onAuthFailed = () => {
            currentUser.value = null;
            if (notifTimer) { clearInterval(notifTimer); notifTimer = null; }
        };

        function openLogin() {
            authMode.value = 'login';
            authUsername.value = '';
            authPassword.value = '';
            showAuthModal.value = true;
        }

        function openRegister() {
            authMode.value = 'register';
            authUsername.value = '';
            authPassword.value = '';
            showAuthModal.value = true;
        }

        function closeAuthModal() {
            showAuthModal.value = false;
            authLoading.value = false;
        }

        async function doLogin() {
            if (!authUsername.value.trim() || !authPassword.value) {
                alert('请输入用户名和密码');
                return;
            }
            try {
                authLoading.value = true;
                const res = await api('/auth/login', {
                    method: 'POST',
                    body: JSON.stringify({
                        username: authUsername.value.trim(),
                        password: authPassword.value
                    })
                });
                if (res.error) {
                    alert(res.error);
                    return;
                }
                if (res.success) {
                    setToken(res.token);
                    setStoredUser(res.user);
                    currentUser.value = res.user;
                    showAuthModal.value = false;
                }
            } catch (e) {
                alert('登录失败：' + (e.message || e));
            } finally {
                authLoading.value = false;
            }
        }

        async function doRegister() {
            if (!authUsername.value.trim() || !authPassword.value) {
                alert('请输入用户名和密码');
                return;
            }
            if (authUsername.value.trim().length < 3) {
                alert('用户名至少3个字符');
                return;
            }
            if (authPassword.value.length < 6) {
                alert('密码至少6位');
                return;
            }
            try {
                authLoading.value = true;
                const res = await api('/auth/register', {
                    method: 'POST',
                    body: JSON.stringify({
                        username: authUsername.value.trim(),
                        password: authPassword.value
                    })
                });
                if (res.error) {
                    alert(res.error);
                    return;
                }
                if (res.success) {
                    setToken(res.token);
                    setStoredUser(res.user);
                    currentUser.value = res.user;
                    showAuthModal.value = false;
                }
            } catch (e) {
                alert('注册失败：' + (e.message || e));
            } finally {
                authLoading.value = false;
            }
        }

        function logout() {
            clearToken();
            clearStoredUser();
            currentUser.value = null;
        }

        function checkCanManage(item) {
            return canManageContent(item, currentUser.value);
        }

        function checkIsAdmin() {
            return isAdmin(currentUser.value);
        }

        function checkIsSuperAdmin() {
            return isSuperAdmin(currentUser.value);
        }

        // Stats
        const stats = ref({ totalPets: 0, totalChallenges: 0, totalElements: 0 });
        const allPetsCache = ref([]);
        
        onMounted(async () => {
            try { stats.value = await api('/stats'); } catch {}

            // 验证 localStorage 中的 token 是否仍然有效（可能已过期）
            if (currentUser.value) {
                try {
                    const me = await api('/auth/me');
                    if (me && me.id) {
                        // token 有效，更新用户信息（角色可能被管理员更改）
                        currentUser.value = me;
                        setStoredUser(me);
                    } else {
                        currentUser.value = null;
                    }
                } catch {
                    // /auth/me 返回 401 → api() 已清除 token 并触发 onAuthFailed
                    currentUser.value = null;
                }
            }

            await loadAllPets();
            searchPets(1);
            searchChallenges();
            loadEvolution();
            loadForumStats();
            loadForumCategories();
            searchForumPosts(1);

            setupPetImageObserver();

            // Dark mode init
            if (darkMode.value) document.documentElement.classList.add('dark');

            // Notifications & favorites init
            if (currentUser.value) {
                loadUnreadCount();
                loadFavorites();
                notifTimer = setInterval(loadUnreadCount, 30000);
            }
        });

        // 组件卸载时清理定时器与观察者，避免内存泄漏
        onUnmounted(() => {
            if (notifTimer) { clearInterval(notifTimer); notifTimer = null; }
            if (petSearchTimer) { clearTimeout(petSearchTimer); petSearchTimer = null; }
            if (petElementTimer) { clearTimeout(petElementTimer); petElementTimer = null; }
            if (petImageObserver) { petImageObserver.disconnect(); petImageObserver = null; }
            onAuthFailed = null;
        });

        let petSearchTimer = null;
        function debouncedSearchPets() {
            if (petSearchTimer) clearTimeout(petSearchTimer);
            petSearchTimer = setTimeout(() => {
                searchPets(1);
            }, 200);
        }

        let petElementTimer = null;
        function debouncedElementFilter() {
            if (petElementTimer) clearTimeout(petElementTimer);
            petElementTimer = setTimeout(() => {
                searchPets(1);
            }, 100);
        }

        let petImageObserver = null;
        let petImageObserverFallback = false; // 浏览器不支持 IntersectionObserver 时降级
        function setupPetImageObserver() {
            if ('IntersectionObserver' in window) {
                petImageObserver = new IntersectionObserver((entries) => {
                    entries.forEach(entry => {
                        if (entry.isIntersecting) {
                            const img = entry.target;
                            const src = img.dataset.src;
                            if (src) {
                                img.src = src;
                                img.removeAttribute('data-src');
                            }
                            petImageObserver.unobserve(img);
                        }
                    });
                }, { rootMargin: '100px' });
            } else {
                // 降级标志：observePetImages 会直接把 data-src 赋给 src
                petImageObserverFallback = true;
            }
        }

        function observePetImages() {
            // 降级路径：无 IntersectionObserver 时立即加载所有图片
            if (petImageObserverFallback) {
                nextTick(() => {
                    document.querySelectorAll('.pet-card img[data-src]').forEach(img => {
                        const src = img.dataset.src;
                        if (src) {
                            img.src = src;
                            img.removeAttribute('data-src');
                        }
                    });
                });
                return;
            }
            if (!petImageObserver) return;
            // 用 nextTick 等待 Vue 完成 DOM 更新，避免 setTimeout 漏观察
            nextTick(() => {
                const imgs = document.querySelectorAll('.pet-card img[data-src]');
                imgs.forEach(img => {
                    if (!img.src || img.getAttribute('data-src')) {
                        petImageObserver.observe(img);
                    }
                });
            });
        }

        // === Pets ===
        const petSearch = ref('');
        const petElement = ref('');
        const petElements = ref([]);
        const petList = ref([]);
        const petPage = ref(1);
        const petTotal = ref(0);
        const petPageSize = 20;
        const petDetail = ref(null);

        // === Evolution (进化专区) ===
        const evoList = ref([]);
        const evoSearch = ref('');
        const evoElement = ref('');
        const evoPage = ref(1);
        const evoPageSize = ref(24);
        const evoSortBy = ref('raceId');
        const evoSortOrder = ref('desc');

        // === Spirit Folders (攻略专区) ===
        const spiritFolderList = ref([]);
        const spiritFolderSearch = ref('');
        const filteredSpiritFolders = ref([]);
        const selectedSpiritFolder = ref(null);
        const spiritVideos = ref([]);
        const showSpiritVideoForm = ref(false);
        const newVideoTitle = ref('');
        const newVideoUrl = ref('');
        const newVideoAuthor = ref('');
        const newVideoNote = ref('');
        const newVideoPic = ref('');
        const newVideoLevels = ref([]);
        const newVideoTags = ref([]);
        const newVideoTagInput = ref('');
        const fetchingBilibili = ref(false);
        const showCoverUploadModal = ref(false);
        const editingVideo = ref(null);
        const editingCoverPic = ref('');
        const fetchingBilibiliCover = ref(false);
        const savingCover = ref(false);
        const videoTagFilter = ref('');
        const allVideoTags = ref([]);

        async function loadAllPets() {
            try {
                const data = await api('/pets?page=1&pageSize=999');
                allPetsCache.value = data.data || [];
                
                // 提取属性列表
                const elements = new Set();
                for (const pet of allPetsCache.value) {
                    const elem = pet.elementTypeName;
                    if (elem) elements.add(elem);
                }
                petElements.value = Array.from(elements).sort();
                
                // 构建灵初文件夹列表（倒序）
                spiritFolderList.value = allPetsCache.value
                    .map(p => ({ ...p, iconError: false, videoCount: 0 }))
                    .sort((a, b) => b.raceId - a.raceId);
                
                // 加载视频数量
                try {
                    const statsData = await api('/videos?page=1&pageSize=999');
                    const allVideos = statsData.data || [];
                    const countMap = {};
                    for (const v of allVideos) {
                        const rid = v.spiritRaceId;
                        if (rid) countMap[rid] = (countMap[rid] || 0) + 1;
                    }
                    for (const p of spiritFolderList.value) {
                        p.videoCount = countMap[p.raceId] || 0;
                    }
                } catch {}
                filterSpiritFolders();
            } catch {
                petElements.value = [];
                spiritFolderList.value = [];
                filteredSpiritFolders.value = [];
            }
        }

        async function searchPets(page) {
            petPage.value = page;
            
            if (allPetsCache.value.length > 0) {
                let filtered = allPetsCache.value;
                
                if (petSearch.value.trim()) {
                    const keyword = petSearch.value.trim().toLowerCase();
                    filtered = filtered.filter(p => p.name.toLowerCase().includes(keyword));
                }
                
                if (petElement.value && petElement.value !== '全部属性') {
                    filtered = filtered.filter(p => p.elementTypeName === petElement.value);
                }
                
                petTotal.value = filtered.length;
                const start = (page - 1) * petPageSize;
                const end = start + petPageSize;
                petList.value = filtered.slice(start, end);
                
                observePetImages();
                return;
            }
            
            const data = await api(`/pets?page=${page}&pageSize=${petPageSize}&keyword=${petSearch.value}&element=${petElement.value}`);
            petList.value = data.data || [];
            petTotal.value = data.total;
            observePetImages();
        }

        async function openPetDetail(raceId) {
            if (allPetsCache.value.length > 0) {
                const cached = allPetsCache.value.find(p => p.raceId === raceId);
                if (cached) {
                    petDetail.value = { ...cached, iconLoaded: false };
                    return;
                }
            }
            petDetail.value = await api(`/pet/${raceId}`);
            petDetail.value.iconLoaded = false;
        }

        function closePetDetail() {
            petDetail.value = null;
        }

        // === Evolution Functions ===
        async function loadEvolution() {
            try {
                evoList.value = await api('/evo');
            } catch {
                evoList.value = [];
            }
        }

        function filteredEvoList() {
            let list = [...evoList.value];
            if (evoSearch.value) {
                const kw = evoSearch.value.toLowerCase();
                list = list.filter(e => e.name.toLowerCase().includes(kw) || String(e.raceId).includes(kw));
            }
            if (evoElement.value) {
                list = list.filter(e => e.element === evoElement.value);
            }
            list.sort((a, b) => {
                const valA = a[evoSortBy.value];
                const valB = b[evoSortBy.value];
                if (valA < valB) return evoSortOrder.value === 'asc' ? -1 : 1;
                if (valA > valB) return evoSortOrder.value === 'asc' ? 1 : -1;
                return 0;
            });
            return list;
        }

        function paginatedEvoList() {
            const list = filteredEvoList();
            const start = (evoPage.value - 1) * evoPageSize.value;
            return list.slice(start, start + evoPageSize.value);
        }

        function evoTotal() {
            return filteredEvoList().length;
        }

        function toggleEvoSort(field) {
            if (evoSortBy.value === field) {
                evoSortOrder.value = evoSortOrder.value === 'asc' ? 'desc' : 'asc';
            } else {
                evoSortBy.value = field;
                evoSortOrder.value = 'desc';
            }
            evoPage.value = 1;
        }

        // === Spirit Folder Functions ===
        async function refreshVideoCounts() {
            try {
                const statsData = await api('/videos?page=1&pageSize=999');
                const allVideos = statsData.data || [];
                const countMap = {};
                for (const v of allVideos) {
                    const rid = v.spiritRaceId;
                    if (rid) countMap[rid] = (countMap[rid] || 0) + 1;
                }
                for (const p of spiritFolderList.value) {
                    p.videoCount = countMap[p.raceId] || 0;
                }
            } catch {}
        }

        function filterSpiritFolders() {
            const kw = spiritFolderSearch.value.toLowerCase().trim();
            if (!kw) {
                filteredSpiritFolders.value = spiritFolderList.value;
            } else {
                filteredSpiritFolders.value = spiritFolderList.value.filter(p =>
                    p.name.toLowerCase().includes(kw) || String(p.raceId).includes(kw)
                );
            }
        }

        async function openSpiritFolder(pet) {
            selectedSpiritFolder.value = { ...pet, iconError: false };
            spiritVideos.value = [];
            showSpiritVideoForm.value = false;
            videoTagFilter.value = '';
            try {
                const data = await api(`/videos/pet/${pet.raceId}`);
                spiritVideos.value = data.videos || [];
                extractAllVideoTags();
            } catch (e) {
                spiritVideos.value = [];
            }
        }

        function extractAllVideoTags() {
            const tags = new Set();
            for (const video of spiritVideos.value) {
                if (video.tags && Array.isArray(video.tags)) {
                    video.tags.forEach(tag => tags.add(tag));
                }
            }
            allVideoTags.value = Array.from(tags);
        }

        function addVideoTag() {
            const tag = newVideoTagInput.value.trim();
            if (tag && !newVideoTags.value.includes(tag)) {
                newVideoTags.value.push(tag);
            }
            newVideoTagInput.value = '';
        }

        function removeVideoTag(tag) {
            newVideoTags.value = newVideoTags.value.filter(t => t !== tag);
        }

        function closeSpiritFolder() {
            selectedSpiritFolder.value = null;
            spiritVideos.value = [];
            showSpiritVideoForm.value = false;
            refreshVideoCounts();
        }

        function getVideoCover(video) {
            if (!video) return '';
            if (video.pic && !video.coverError) {
                if (video.pic.startsWith('http') && video.pic.includes('hdslb.com')) {
                    return '/api/bilibili-image?url=' + encodeURIComponent(video.pic);
                }
                return video.pic;
            }
            return '';
        }

        async function fetchBilibiliInfo() {
            if (!newVideoUrl.value.trim()) {
                alert('请先输入视频链接');
                return;
            }
            try {
                fetchingBilibili.value = true;
                const result = await api('/bilibili/video-info?url=' + encodeURIComponent(newVideoUrl.value));
                if (result.error) {
                    alert('获取失败：' + result.error);
                    return;
                }
                if (result.title) newVideoTitle.value = result.title;
                if (result.author) newVideoAuthor.value = result.author;
                if (result.pic) {
                    newVideoPic.value = '/api/bilibili-image?url=' + encodeURIComponent(result.pic);
                }
            } catch (e) {
                alert('获取失败：' + (e.message || e));
            } finally {
                fetchingBilibili.value = false;
            }
        }

        async function handleNewVideoCoverUpload(event) {
            const file = event.target.files[0];
            if (!file) return;
            try {
                const result = await uploadImage(file);
                if (result.url) {
                    newVideoPic.value = result.url;
                } else if (result.error) {
                    alert('上传失败：' + result.error);
                }
            } catch (e) {
                alert('上传失败：' + (e.message || e));
            }
            event.target.value = '';
        }

        function showVideoCoverUpload(video) {
            editingVideo.value = video;
            editingCoverPic.value = '';
            showCoverUploadModal.value = true;
        }

        function closeCoverUploadModal() {
            showCoverUploadModal.value = false;
            editingVideo.value = null;
            editingCoverPic.value = '';
        }

        async function handleEditVideoCoverUpload(event) {
            const file = event.target.files[0];
            if (!file) return;
            try {
                const result = await uploadImage(file);
                if (result.url) {
                    editingCoverPic.value = result.url;
                } else if (result.error) {
                    alert('上传失败：' + result.error);
                }
            } catch (e) {
                alert('上传失败：' + (e.message || e));
            }
            event.target.value = '';
        }

        async function fetchBilibiliCoverForEdit() {
            if (!editingVideo.value || !editingVideo.value.url) return;
            try {
                fetchingBilibiliCover.value = true;
                const result = await api('/bilibili/video-info?url=' + encodeURIComponent(editingVideo.value.url));
                if (result.error) {
                    alert('获取失败：' + result.error);
                    return;
                }
                if (result.pic) {
                    editingCoverPic.value = '/api/bilibili-image?url=' + encodeURIComponent(result.pic);
                }
            } catch (e) {
                alert('获取失败：' + (e.message || e));
            } finally {
                fetchingBilibiliCover.value = false;
            }
        }

        async function saveVideoCover() {
            if (!editingVideo.value || !selectedSpiritFolder.value) return;
            if (!editingCoverPic.value) {
                alert('请先选择或上传封面');
                return;
            }
            try {
                savingCover.value = true;
                const videoId = editingVideo.value.bvid || editingVideo.value.url;
                const result = await api(`/videos/pet/${selectedSpiritFolder.value.raceId}/${encodeURIComponent(videoId)}`, {
                    method: 'PUT',
                    body: JSON.stringify({ pic: editingCoverPic.value })
                });
                if (result.success) {
                    const idx = spiritVideos.value.findIndex(v => (v.bvid || v.url) === videoId);
                    if (idx >= 0) {
                        spiritVideos.value[idx] = result.video;
                        spiritVideos.value[idx].coverError = false;
                    }
                    closeCoverUploadModal();
                } else {
                    alert('保存失败');
                }
            } catch (e) {
                alert('保存失败：' + (e.message || e));
            } finally {
                savingCover.value = false;
            }
        }

        function addLevel() {
            newVideoLevels.value.push({
                id: Date.now(),
                name: '',
                code: ''
            });
        }

        function removeLevel(index) {
            newVideoLevels.value.splice(index, 1);
        }

        async function copyLevelCode(code) {
            try {
                await navigator.clipboard.writeText(code);
                alert('代码已复制到剪贴板！');
            } catch {
                const textarea = document.createElement('textarea');
                textarea.value = code;
                document.body.appendChild(textarea);
                textarea.select();
                document.execCommand('copy');
                document.body.removeChild(textarea);
                alert('代码已复制到剪贴板！');
            }
        }

        async function addSpiritVideo() {
            if (!currentUser.value) {
                alert('请先登录');
                openLogin();
                return;
            }
            if (!newVideoTitle.value.trim() || !newVideoUrl.value.trim()) return;
            try {
                const result = await api(`/videos/pet/${selectedSpiritFolder.value.raceId}`, {
                    method: 'POST',
                    headers: { 'Content-Type': 'application/json' },
                    body: JSON.stringify({
                        title: newVideoTitle.value,
                        url: newVideoUrl.value,
                        author: newVideoAuthor.value,
                        note: newVideoNote.value,
                        pic: newVideoPic.value,
                        levels: newVideoLevels.value,
                        tags: newVideoTags.value
                    })
                });
                if (result.success) {
                    spiritVideos.value.push(result.video);
                    newVideoTitle.value = '';
                    newVideoUrl.value = '';
                    newVideoAuthor.value = '';
                    newVideoNote.value = '';
                    newVideoPic.value = '';
                    newVideoLevels.value = [];
                    newVideoTags.value = [];
                    extractAllVideoTags();
                    showSpiritVideoForm.value = false;
                } else if (result.error) {
                    alert(result.error);
                }
            } catch (e) {
                alert('添加失败');
            }
        }

        async function deleteSpiritVideo(video) {
            if (!confirm('确定删除这个攻略吗？')) return;
            const videoId = video.bvid || video.url;
            try {
                const result = await api(`/videos/pet/${selectedSpiritFolder.value.raceId}/${videoId}`, {
                    method: 'DELETE'
                });
                if (result.success) {
                    spiritVideos.value = spiritVideos.value.filter(v => v.bvid !== videoId && v.url !== videoId);
                } else if (result.error) {
                    alert(result.error);
                }
            } catch (e) {
                alert('删除失败');
            }
        }

        function handleFolderImgError(event, pet) {
            pet.iconError = true;
        }
        function handleFolderImgLoad(event, pet) {
            pet.iconError = false;
        }

        function handleImgError(event, pet) {
            pet.iconError = true;
        }
        function handleImgLoad(event, pet) {
            pet.iconError = false;
        }

        // 安全取首字符，避免空字符串导致 [0] 为 undefined / 渲染异常
        function firstChar(str) {
            return (str && typeof str === 'string') ? str.charAt(0) : '?';
        }

        // === Challenges ===
        const challengeSearch = ref('');
        const challengeList = ref([]);
        const challengeDetail = ref({
            petName: '',
            file: '',
            date: '',
            category: '',
            rules: [],
            entries: [],
            levelStructure: [],
            formations: {}
        });
        const activeFormationTab = ref(0);

        async function searchChallenges() {
            challengeList.value = await api(`/challenges?keyword=${challengeSearch.value}`);
        }

        async function openChallengeDetail(name) {
            challengeDetail.value = await api(`/challenge/${encodeURIComponent(name)}`);
            activeFormationTab.value = 0;
        }

        function closeChallengeDetail() {
            challengeDetail.value = {
                petName: '',
                file: '',
                date: '',
                category: '',
                rules: [],
                entries: [],
                levelStructure: [],
                formations: {}
            };
        }

        const downloadLongImgLoading = ref(false);
        const downloadLongImgMode = ref(false);
        const downloadLongImgProgress = ref('');
        async function downloadChallengeLongImg() {
            const el = document.getElementById('challenge-content');
            if (!el) return;
            if (typeof html2canvas === 'undefined') {
                alert('图片生成库加载失败，请刷新页面后重试');
                return;
            }
            downloadLongImgLoading.value = true;
            const originalTab = activeFormationTab.value;
            const savedStyles = [];
            const blobUrls = [];
            const abortController = new AbortController();
            try {
                downloadLongImgMode.value = true;
                await new Promise(resolve => setTimeout(resolve, 100));
                if (Vue.nextTick) await Vue.nextTick();
                await new Promise(resolve => setTimeout(resolve, 300));

                // 收集所有图片并预加载为本地 Blob URL（解决 CORS 导致 html2canvas 卡死的问题）
                const imgs = Array.from(el.querySelectorAll('img'));
                const validImgs = imgs.filter(img => img.src && !img.src.startsWith('data:') && !img.classList.contains('hidden'));
                downloadLongImgProgress.value = `准备图片... (0/${validImgs.length})`;

                const timeout = ms => new Promise((_, reject) => setTimeout(() => reject(new Error('超时')), ms));
                const imgTimeout = 30000 / Math.max(1, validImgs.length);

                for (let i = 0; i < validImgs.length; i++) {
                    const img = validImgs[i];
                    downloadLongImgProgress.value = `加载图片... (${i + 1}/${validImgs.length})`;
                    try {
                        const resp = await Promise.race([
                            fetch(img.src, { cache: 'force-cache', mode: 'cors' }),
                            timeout(Math.max(5000, imgTimeout)),
                        ]);
                        if (resp.ok) {
                            const blob = await resp.blob();
                            const blobUrl = URL.createObjectURL(blob);
                            blobUrls.push({ el: img, originalSrc: img.src, blobUrl });
                            img.src = blobUrl;
                        }
                    } catch (e) {
                        // 单张图片加载失败不阻塞整体流程
                        console.warn('图片预加载失败:', img.src, e.message);
                    }
                }

                // 再次等待确保所有替换后的 img 完成加载
                downloadLongImgProgress.value = `等待图片渲染...`;
                const finalImgPromises = validImgs.map(img => {
                    if (img.complete) return Promise.resolve();
                    return new Promise(resolve => {
                        img.onload = resolve;
                        img.onerror = resolve;
                    });
                });
                await Promise.race([Promise.all(finalImgPromises), timeout(10000)]);

                const modal = el.closest('.challenge-modal');
                const originalOverflow = modal ? modal.style.overflow : '';
                const originalMaxHeight = modal ? modal.style.maxHeight : '';
                if (modal) {
                    modal.style.overflow = 'visible';
                    modal.style.maxHeight = 'none';
                }

                const gridCells = el.querySelectorAll('.grid-cell');
                gridCells.forEach(cell => {
                    const width = cell.offsetWidth;
                    savedStyles.push({
                        el: cell,
                        width: cell.style.width,
                        height: cell.style.height,
                        flex: cell.style.flex
                    });
                    cell.style.width = width + 'px';
                    cell.style.height = width + 'px';
                    cell.style.flex = 'none';
                });

                const supportItems = el.querySelectorAll('.support-item');
                supportItems.forEach(item => {
                    const width = item.offsetWidth;
                    savedStyles.push({
                        el: item,
                        width: item.style.width,
                        height: item.style.height
                    });
                    item.style.width = width + 'px';
                    item.style.flex = 'none';
                });

                const cellImgs = el.querySelectorAll('.grid-cell img');
                cellImgs.forEach(img => {
                    savedStyles.push({
                        el: img,
                        width: img.style.width,
                        height: img.style.height,
                        maxHeight: img.style.maxHeight
                    });
                    const w = img.offsetWidth;
                    img.style.width = w + 'px';
                    img.style.height = w + 'px';
                    img.style.maxHeight = 'none';
                });

                const supportImgs = el.querySelectorAll('.support-item img');
                supportImgs.forEach(img => {
                    savedStyles.push({
                        el: img,
                        width: img.style.width,
                        height: img.style.height
                    });
                    const w = img.offsetWidth;
                    img.style.width = w + 'px';
                    img.style.height = w + 'px';
                });

                downloadLongImgProgress.value = '生成图片中...';

                const canvasPromise = html2canvas(el, {
                    backgroundColor: '#faf8f5',
                    scale: 2,
                    useCORS: false,
                    allowTaint: true,
                    logging: false,
                    ignoreElements: e => abortController.signal.aborted,
                });
                // 大幅增加超时时间，复杂阵容内容多需要更多时间
                const canvas = await Promise.race([canvasPromise, timeout(60000)]);

                savedStyles.forEach(s => {
                    s.el.style.width = s.width;
                    s.el.style.height = s.height;
                    if (s.flex !== undefined) s.el.style.flex = s.flex;
                    if (s.maxHeight !== undefined) s.el.style.maxHeight = s.maxHeight;
                });

                if (modal) {
                    modal.style.overflow = originalOverflow;
                    modal.style.maxHeight = originalMaxHeight;
                }

                downloadLongImgProgress.value = '下载中...';
                const link = document.createElement('a');
                link.download = (challengeDetail.value.petName || challengeDetail.value.file || 'challenge') + '_长图.png';
                link.href = canvas.toDataURL('image/png');
                link.click();
                activeFormationTab.value = originalTab;
            } catch (err) {
                abortController.abort();
                console.error('生成图片失败:', err);
                alert('生成图片失败' + (err.message === '超时' ? '（超时）：图片内容较多，请减少阵容组数后再试，或稍后重试' : '：' + err.message));
                savedStyles.forEach(s => {
                    s.el.style.width = s.width;
                    s.el.style.height = s.height;
                    if (s.flex !== undefined) s.el.style.flex = s.flex;
                    if (s.maxHeight !== undefined) s.el.style.maxHeight = s.maxHeight;
                });
            } finally {
                // 恢复图片原始 src，并释放 Blob URL
                blobUrls.forEach(item => {
                    try { item.el.src = item.originalSrc; } catch (_) {}
                    try { URL.revokeObjectURL(item.blobUrl); } catch (_) {}
                });
                downloadLongImgMode.value = false;
                downloadLongImgLoading.value = false;
                downloadLongImgProgress.value = '';
            }
        }

        function getFormationCount(detail) {
            if (!detail || !detail.formations) return 0;
            return Object.keys(detail.formations).length;
        }

        function getCurrentLevelStructure(detail, tab) {
            if (!detail || !detail.levelStructure || !detail.levelStructure[tab]) return null;
            return detail.levelStructure[tab];
        }

        function getTeamIds(detail, tab) {
            const ls = getCurrentLevelStructure(detail, tab);
            return ls ? ls.teamIds : [];
        }

        function getFormationKeys(detail) {
            if (!detail || !detail.formations) return [];
            return Object.keys(detail.formations);
        }

        function formatExtraProperty(formation) {
            if (!formation || !formation.extraProperty) return [];
            const raw = String(formation.extraProperty).trim();
            if (!raw) return [];
            return raw.split('#').map(item => {
                const s = item.trim();
                if (!s) return null;
                const m = s.match(/^(.+?)([+-])(\d+)$/);
                if (m) {
                    return { label: m[1], sign: m[2], value: m[3], raw: s };
                }
                return { label: s, sign: null, value: null, raw: s };
            }).filter(Boolean);
        }

        function getBattlePet(formation, position) {
            if (!formation || !formation.pets) return null;
            const envoyIds = formation.pets
                .map(p => p.cepi || p.contractPetSlotId)
                .filter(id => id !== undefined && id !== null);
            const ps = formation.positionString || formation.ps;
            if (!ps) {
                return formation.pets.find(p => 
                    p.slotId === position && 
                    !envoyIds.includes(p.slotId)
                );
            }
            const positions = ps.split('#');
            const positionToPsIndex = [0, 3, 6, 1, 4, 7, 2, 5, 8];
            const psIndex = positionToPsIndex[position - 1];
            if (psIndex >= positions.length) return null;
            const petId = parseInt(positions[psIndex]);
            if (petId <= 0) return null;
            const pet = formation.pets.find(p => p.slotId === petId || p.id === petId);
            if (!pet) return null;
            if (envoyIds.includes(pet.slotId) || envoyIds.includes(pet.id)) return null;
            return pet;
        }

        function getSupportPets(formation) {
            if (!formation || !formation.pets) return [];
            const envoyIds = formation.pets
                .map(p => p.cepi || p.contractPetSlotId)
                .filter(id => id !== undefined && id !== null);
            return formation.pets.filter(p => 
                envoyIds.includes(p.slotId) || envoyIds.includes(p.id)
            );
        }

        function isPetMarked(pet) {
            if (!pet) return false;
            if (pet.isMarked !== undefined) return pet.isMarked;
            const slotId = pet.slotId !== undefined ? pet.slotId : pet.id;
            if (slotId === undefined || slotId === null) return false;
            return String(slotId).startsWith('1000');
        }

        function handleGridImgError(event, pet) {
            if (pet) pet.iconError = true;
        }

        function handleGridImgLoad(event, pet) {
            if (pet) pet.iconError = false;
        }

        // === Utility ===
        function formatPlayCount(play) {
            if (!play) return '0';
            if (play >= 10000) return (play / 10000).toFixed(1) + '万';
            return play.toString();
        }

        function formatSpiritName(name) {
            if (!name) return '';
            return name.replace('[灵初]', '').replace('[星迹]', '').replace('[神运]', '');
        }

        function formatTime(dt) {
            if (!dt) return '';
            const d = new Date(dt);
            if (isNaN(d.getTime())) return dt;
            const now = new Date();
            const diff = (now - d) / 1000;
            if (diff < 60) return '刚刚';
            if (diff < 3600) return Math.floor(diff / 60) + '分钟前';
            if (diff < 86400) return Math.floor(diff / 3600) + '小时前';
            if (diff < 604800) return Math.floor(diff / 86400) + '天前';
            return d.toLocaleDateString();
        }

        // === Forum ===
        const forumStats = ref({ totalPosts: 0, totalComments: 0, todayPosts: 0 });
        const forumCategories = ref([]);
        const forumCategory = ref('');
        const forumSort = ref('latest');
        const forumSearch = ref('');
        const forumPosts = ref([]);
        const forumPage = ref(1);
        const forumTotal = ref(0);
        const forumPageSize = 20;
        const forumDetailPost = ref(null);
        const forumComments = ref([]);
        const forumCommentTotal = ref(0);
        const showPostForm = ref(false);
        const newPostTitle = ref('');
        const newPostContent = ref('');
        const newPostAuthor = ref('');
        const newPostCategory = ref('综合讨论');
        const newPostImages = ref([]);
        const uploadingImage = ref(false);
        const newCommentContent = ref('');
        const newCommentAuthor = ref('');
        const newCommentImages = ref([]);
        const uploadingCommentImage = ref(false);

        async function loadForumStats() {
            try {
                forumStats.value = await api('/forum/stats');
            } catch {}
        }

        async function loadForumCategories() {
            try {
                forumCategories.value = await api('/forum/categories');
            } catch {
                forumCategories.value = ['综合讨论', '攻略分享', '精灵培养', '阵容搭配', '问题求助', '闲聊灌水'];
            }
        }

        async function searchForumPosts(page) {
            forumPage.value = page;
            const params = new URLSearchParams({
                page: page,
                pageSize: forumPageSize,
                sort: forumSort.value
            });
            if (forumCategory.value) params.set('category', forumCategory.value);
            if (forumSearch.value) params.set('keyword', forumSearch.value);
            try {
                const data = await api('/forum/posts?' + params.toString());
                forumPosts.value = data.data || [];
                forumTotal.value = data.total || 0;
            } catch {
                forumPosts.value = [];
                forumTotal.value = 0;
            }
        }

        async function openForumPost(postId) {
            try {
                forumDetailPost.value = await api(`/forum/post/${postId}`);
                loadForumComments(postId);
            } catch {}
        }

        function closeForumPost() {
            forumDetailPost.value = null;
            forumComments.value = [];
            forumCommentTotal.value = 0;
        }

        async function loadForumComments(postId) {
            try {
                const data = await api(`/forum/post/${postId}/comments?page=1&pageSize=50`);
                forumComments.value = data.data || [];
                forumCommentTotal.value = data.total || 0;
            } catch {
                forumComments.value = [];
                forumCommentTotal.value = 0;
            }
        }

        async function handlePostImageUpload(event) {
            const files = event.target.files;
            if (!files || files.length === 0) return;
            
            for (const file of files) {
                if (newPostImages.value.length >= 9) {
                    alert('最多上传9张图片');
                    break;
                }
                try {
                    uploadingImage.value = true;
                    const result = await uploadImage(file);
                    if (result.url) {
                        newPostImages.value.push(result.url);
                    } else if (result.error) {
                        alert('上传失败：' + result.error);
                    }
                } catch (e) {
                    alert('上传失败：' + (e.message || e));
                } finally {
                    uploadingImage.value = false;
                }
            }
            event.target.value = '';
        }

        function removePostImage(index) {
            newPostImages.value.splice(index, 1);
        }

        async function handleCommentImageUpload(event) {
            const files = event.target.files;
            if (!files || files.length === 0) return;
            
            for (const file of files) {
                if (newCommentImages.value.length >= 9) {
                    alert('最多上传9张图片');
                    break;
                }
                try {
                    uploadingCommentImage.value = true;
                    const result = await uploadImage(file);
                    if (result.url) {
                        newCommentImages.value.push(result.url);
                    } else if (result.error) {
                        alert('上传失败：' + result.error);
                    }
                } catch (e) {
                    alert('上传失败：' + (e.message || e));
                } finally {
                    uploadingCommentImage.value = false;
                }
            }
            event.target.value = '';
        }

        function removeCommentImage(index) {
            newCommentImages.value.splice(index, 1);
        }

        function parseImages(images) {
            if (!images) return [];
            if (Array.isArray(images)) return images;
            try {
                return JSON.parse(images);
            } catch {
                return [];
            }
        }

        async function submitPost() {
            if (!currentUser.value) {
                alert('请先登录');
                openLogin();
                return;
            }
            if (!newPostTitle.value.trim() || !newPostContent.value.trim()) {
                alert('标题和内容不能为空');
                return;
            }
            try {
                await api('/forum/post', {
                    method: 'POST',
                    body: JSON.stringify({
                        title: newPostTitle.value,
                        content: newPostContent.value,
                        category: newPostCategory.value,
                        images: newPostImages.value
                    })
                });
                showPostForm.value = false;
                newPostTitle.value = '';
                newPostContent.value = '';
                newPostAuthor.value = '';
                newPostImages.value = [];
                searchForumPosts(1);
                loadForumStats();
            } catch (e) {
                alert('发布失败：' + (e.message || e));
            }
        }

        async function deleteForumPost(postId) {
            if (!confirm('确定要删除这篇帖子吗？')) return;
            try {
                const res = await api(`/forum/post/${postId}`, { method: 'DELETE' });
                if (res.error) {
                    alert(res.error);
                    return;
                }
                if (forumDetailPost.value && forumDetailPost.value.id === postId) {
                    closeForumPost();
                }
                searchForumPosts(1);
                loadForumStats();
            } catch (e) {
                alert('删除失败：' + (e.message || e));
            }
        }

        async function submitComment() {
            if (!currentUser.value) {
                alert('请先登录');
                openLogin();
                return;
            }
            if (!newCommentContent.value.trim() || !forumDetailPost.value) return;
            try {
                await api(`/forum/post/${forumDetailPost.value.id}/comment`, {
                    method: 'POST',
                    body: JSON.stringify({
                        content: newCommentContent.value,
                        images: newCommentImages.value
                    })
                });
                newCommentContent.value = '';
                newCommentAuthor.value = '';
                newCommentImages.value = [];
                loadForumComments(forumDetailPost.value.id);
                forumDetailPost.value.comment_count++;
            } catch (e) {
                alert('评论失败：' + (e.message || e));
            }
        }

        async function deleteForumComment(commentId) {
            if (!confirm('确定要删除这条评论吗？')) return;
            try {
                const res = await api(`/forum/comment/${commentId}`, { method: 'DELETE' });
                if (res.error) {
                    alert(res.error);
                    return;
                }
                loadForumComments(forumDetailPost.value.id);
                if (forumDetailPost.value) {
                    forumDetailPost.value.comment_count--;
                }
            } catch (e) {
                alert('删除失败：' + (e.message || e));
            }
        }

        async function likeForumPost(postId) {
            try {
                const res = await api(`/forum/post/${postId}/like`, { method: 'POST' });
                if (forumDetailPost.value && forumDetailPost.value.id === postId) {
                    forumDetailPost.value.likes = res.likes;
                }
                const post = forumPosts.value.find(p => p.id === postId);
                if (post) post.likes = res.likes;
            } catch {}
        }

        async function likeForumComment(commentId) {
            try {
                const res = await api(`/forum/comment/${commentId}/like`, { method: 'POST' });
                const c = forumComments.value.find(x => x.id === commentId);
                if (c) c.likes = res.likes;
            } catch {}
        }

        // === Avatar Upload ===
        const showAvatarUpload = ref(false);
        const avatarUploading = ref(false);

        async function uploadAvatar() {
            const input = document.createElement('input');
            input.type = 'file';
            input.accept = 'image/*';
            input.onchange = async (e) => {
                const file = e.target.files[0];
                if (!file) return;
                const formData = new FormData();
                formData.append('file', file);
                try {
                    avatarUploading.value = true;
                    const res = await api('/auth/avatar', {
                        method: 'POST',
                        body: formData
                    });
                    if (res.url) {
                        currentUser.value.avatar = res.url;
                        setStoredUser(currentUser.value);
                    }
                } catch (e) {
                    alert('头像上传失败');
                } finally {
                    avatarUploading.value = false;
                }
            };
            input.click();
        }

        // === Change Password ===
        const showPasswordModal = ref(false);
        const oldPassword = ref('');
        const newPassword = ref('');
        const confirmPassword = ref('');

        async function changePassword() {
            if (!oldPassword.value || !newPassword.value) {
                alert('请填写完整');
                return;
            }
            if (newPassword.value !== confirmPassword.value) {
                alert('两次密码不一致');
                return;
            }
            if (newPassword.value.length < 6) {
                alert('新密码至少6位');
                return;
            }
            try {
                const res = await api('/auth/password', {
                    method: 'PUT',
                    body: JSON.stringify({
                        old_password: oldPassword.value,
                        new_password: newPassword.value
                    })
                });
                if (res.success) {
                    alert('密码修改成功，请重新登录');
                    logout();
                    showPasswordModal.value = false;
                } else if (res.error) {
                    alert(res.error);
                }
            } catch (e) {
                alert('修改失败');
            }
        }

        // === Admin Panel ===
        const showAdminPanel = ref(false);
        const adminUsers = ref([]);
        const adminUsersPage = ref(1);
        const adminUsersTotal = ref(0);
        const adminSearchKeyword = ref('');

        async function loadAdminUsers(page) {
            adminUsersPage.value = page || 1;
            const res = await api(`/auth/users?page=${adminUsersPage.value}&keyword=${adminSearchKeyword.value}`);
            adminUsers.value = res.data || [];
            adminUsersTotal.value = res.total || 0;
        }

        async function changeUserRole(userId, newRole) {
            if (!confirm(`确定将此用户角色改为${newRole === 'super_admin' ? '超级管理员' : newRole === 'admin' ? '管理员' : '普通用户'}？`)) return;
            const res = await api(`/auth/user/${userId}/role`, {
                method: 'PUT',
                body: JSON.stringify({ role: newRole })
            });
            if (res.success) {
                loadAdminUsers(adminUsersPage.value);
            } else if (res.error) {
                alert(res.error);
            }
        }

        // === User Profile ===
        const showUserProfile = ref(false);
        const profileUser = ref(null);
        const profilePosts = ref([]);
        const profileComments = ref([]);
        const currentProfileTab = ref('posts');

        async function openUserProfile(userId) {
            const res = await api(`/auth/user/${userId}/profile`);
            profileUser.value = res.user;
            profilePosts.value = res.posts || [];
            profileComments.value = res.comments || [];
            showUserProfile.value = true;
        }

        // === Post Edit ===
        const editingPost = ref(null);
        const editPostTitle = ref('');
        const editPostContent = ref('');
        const editPostCategory = ref('');

        function startEditPost(post) {
            editingPost.value = post.id;
            editPostTitle.value = post.title;
            editPostContent.value = post.content;
            editPostCategory.value = post.category;
        }

        function cancelEditPost() {
            editingPost.value = null;
        }

        async function saveEditPost() {
            const postId = editingPost.value;
            const res = await api(`/forum/post/${postId}`, {
                method: 'PUT',
                body: JSON.stringify({
                    title: editPostTitle.value,
                    content: editPostContent.value,
                    category: editPostCategory.value
                })
            });
            if (res.success) {
                editingPost.value = null;
                openForumPost(postId);
            } else if (res.error) {
                alert(res.error);
            }
        }

        // === Post Pin/Feature ===
        async function pinPost(postId, pinned) {
            const res = await api(`/forum/post/${postId}/pin`, {
                method: 'PUT',
                body: JSON.stringify({ is_pinned: pinned })
            });
            if (res.success) searchForumPosts(forumPage.value);
        }

        async function featurePost(postId, featured) {
            const res = await api(`/forum/post/${postId}/feature`, {
                method: 'PUT',
                body: JSON.stringify({ is_featured: featured })
            });
            if (res.success) searchForumPosts(forumPage.value);
        }

        // === Favorites ===
        const favoritePosts = ref([]);

        async function toggleFavorite(postId) {
            const isFav = favoritePosts.value.some(p => p.id === postId);
            const res = await api(`/forum/post/${postId}/favorite`, {
                method: isFav ? 'DELETE' : 'POST'
            });
            if (res.success) {
                loadFavorites();
            }
        }

        async function loadFavorites() {
            const res = await api('/forum/favorites');
            favoritePosts.value = res.data || [];
        }

        // === Comment Reply ===
        const replyToComment = ref(null);

        function setReplyTo(comment) {
            replyToComment.value = comment;
        }

        function cancelReply() {
            replyToComment.value = null;
        }

        // === Video Like & Comments ===
        const videoComments = ref([]);
        const newVideoComment = ref('');
        const showVideoComments = ref(false);
        const currentVideoForComments = ref(null);

        async function likeVideo(raceId, videoId) {
            const res = await api(`/videos/${raceId}/${videoId}/like`, { method: 'POST' });
            if (res.success) {
                const v = spiritVideos.value.find(v => (v.bvid || v.url) === videoId);
                if (v) v.likes = (v.likes || 0) + 1;
            }
        }

        async function loadVideoComments(raceId, videoId) {
            const res = await api(`/videos/${raceId}/${videoId}/comments`);
            videoComments.value = res.data || [];
        }

        async function addVideoComment(raceId, videoId) {
            if (!currentUser.value) { openLogin(); return; }
            if (!newVideoComment.value.trim()) return;
            const res = await api(`/videos/${raceId}/${videoId}/comments`, {
                method: 'POST',
                body: JSON.stringify({ content: newVideoComment.value })
            });
            if (res.success) {
                newVideoComment.value = '';
                loadVideoComments(raceId, videoId);
            }
        }

        async function deleteVideoComment(commentId) {
            if (!confirm('确定删除此评论？')) return;
            const res = await api(`/videos/comment/${commentId}`, { method: 'DELETE' });
            if (res.success && currentVideoForComments.value) {
                loadVideoComments(currentVideoForComments.value.raceId, currentVideoForComments.value.videoId);
            }
        }

        // === Notifications ===
        const notifications = ref([]);
        const unreadCount = ref(0);
        const showNotifications = ref(false);

        async function loadNotifications() {
            const res = await api('/notifications');
            notifications.value = res.data || [];
        }

        async function loadUnreadCount() {
            try {
                const res = await api('/notifications/unread-count');
                unreadCount.value = res.count || 0;
            } catch {}
        }

        async function markNotificationRead(notifId) {
            await api(`/notifications/${notifId}/read`, { method: 'PUT' });
            loadNotifications();
            loadUnreadCount();
        }

        async function markAllRead() {
            await api('/notifications/read-all', { method: 'PUT' });
            loadNotifications();
            loadUnreadCount();
        }

        let notifTimer = null;

        // === Audit Logs ===
        const showAuditLogs = ref(false);
        const auditLogs = ref([]);
        const auditLogsPage = ref(1);

        async function loadAuditLogs(page) {
            auditLogsPage.value = page || 1;
            const res = await api(`/audit-logs?page=${auditLogsPage.value}`);
            auditLogs.value = res.data || [];
        }

        // === Database Backup ===
        async function backupDatabase() {
            if (!confirm('确定备份数据库？')) return;
            const res = await api('/backup', { method: 'POST' });
            if (res.success) {
                alert('备份成功: ' + res.filename);
            } else if (res.error) {
                alert('备份失败: ' + res.error);
            }
        }

        // === Formation Simulator ===
        const showFormationSim = ref(false);
        const formationSlots = ref(Array(9).fill(null));
        const formationSearchKeyword = ref('');
        const formationSearchResults = ref([]);

        function addToFormation(pos, pet) {
            formationSlots.value[pos] = pet;
        }

        function removeFromFormation(pos) {
            formationSlots.value[pos] = null;
        }

        function handleDragStart(event, pet, pos) {
            event.dataTransfer.setData('text/plain', JSON.stringify({ pet, fromPos: pos }));
            event.dataTransfer.effectAllowed = 'move';
        }

        function handleDrop(pos, event) {
            const data = JSON.parse(event.dataTransfer.getData('text/plain'));
            const pet = data.pet;
            const fromPos = data.fromPos;
            
            if (fromPos >= 0) {
                formationSlots.value[fromPos] = null;
            }
            formationSlots.value[pos] = pet;
        }

        async function searchPetsForFormation(keyword) {
            if (!keyword) { formationSearchResults.value = []; return; }
            const res = await api(`/pet/search?keyword=${keyword}`);
            formationSearchResults.value = res || [];
        }

        const debounceSearchForFormation = debounce(searchPetsForFormation, 200);

        function clearFormation() {
            formationSlots.value = Array(9).fill(null);
        }

        function getSimFormationCount(slots) {
            return slots.filter(p => p).length;
        }

        function getFormationElements(slots) {
            const elems = new Set();
            slots.filter(p => p).forEach(p => {
                if (p.elementTypeName) elems.add(p.elementTypeName);
            });
            return Array.from(elems);
        }

        // === Lightbox ===
        const lightboxImages = ref([]);
        const lightboxIndex = ref(0);
        const showLightbox = ref(false);

        function openLightbox(images, index) {
            lightboxImages.value = images;
            lightboxIndex.value = index || 0;
            showLightbox.value = true;
        }

        function closeLightbox() {
            showLightbox.value = false;
        }

        function prevLightbox() {
            lightboxIndex.value = (lightboxIndex.value - 1 + lightboxImages.value.length) % lightboxImages.value.length;
        }

        function nextLightbox() {
            lightboxIndex.value = (lightboxIndex.value + 1) % lightboxImages.value.length;
        }

        // === Skeleton Loading ===
        const loadingPets = ref(false);
        const loadingForum = ref(false);
        const loadingVideos = ref(false);

        // === Page Transition ===
        const pageTransitionName = ref('fade');

        // === Dark Mode ===
        const darkMode = ref(localStorage.getItem('aoqi_dark_mode') === 'true');

        function toggleDarkMode() {
            darkMode.value = !darkMode.value;
            localStorage.setItem('aoqi_dark_mode', darkMode.value);
            document.documentElement.classList.toggle('dark', darkMode.value);
        }

        // === Report ===
        const showReportModal = ref(false);
        const reportTarget = ref(null);
        const reportReason = ref('');

        function openReport(type, id) {
            reportTarget.value = { type, id };
            reportReason.value = '';
            showReportModal.value = true;
        }

        async function submitReport() {
            if (!currentUser.value) { openLogin(); return; }
            alert('举报已提交，管理员将尽快处理');
            showReportModal.value = false;
            reportReason.value = '';
        }

        // Modify submitComment to support replyToComment
        async function submitCommentWithReply() {
            if (!currentUser.value) {
                alert('请先登录');
                openLogin();
                return;
            }
            if (!newCommentContent.value.trim() || !forumDetailPost.value) return;
            try {
                const body = {
                    content: newCommentContent.value,
                    images: newCommentImages.value
                };
                if (replyToComment.value) {
                    body.parent_id = replyToComment.value.id;
                }
                await api(`/forum/post/${forumDetailPost.value.id}/comment`, {
                    method: 'POST',
                    body: JSON.stringify(body)
                });
                newCommentContent.value = '';
                newCommentAuthor.value = '';
                newCommentImages.value = [];
                replyToComment.value = null;
                loadForumComments(forumDetailPost.value.id);
                forumDetailPost.value.comment_count++;
            } catch (e) {
                alert('评论失败：' + (e.message || e));
            }
        }

        // Override submitComment with reply support
        const _origSubmitComment = submitComment;
        submitComment = submitCommentWithReply;

        return {
            currentPage, sidebarCollapsed, navItems, stats,
            petSearch, petElement, petElements, petList, petPage, petTotal, petPageSize, petDetail,
            searchPets, openPetDetail, closePetDetail, handleImgError, handleImgLoad, firstChar,
            debouncedSearchPets, debouncedElementFilter,
            evoList, evoSearch, evoElement, evoPage, evoPageSize, evoSortBy, evoSortOrder,
            filteredEvoList, paginatedEvoList, evoTotal, toggleEvoSort,
            spiritFolderList, spiritFolderSearch, filteredSpiritFolders, selectedSpiritFolder,
            spiritVideos, showSpiritVideoForm, newVideoTitle, newVideoUrl, newVideoAuthor, newVideoNote, newVideoPic, newVideoLevels, newVideoTags, newVideoTagInput, fetchingBilibili,
            showCoverUploadModal, editingVideo, editingCoverPic, fetchingBilibiliCover, savingCover,
            videoTagFilter, allVideoTags,
            filterSpiritFolders, openSpiritFolder, closeSpiritFolder,
            addSpiritVideo, deleteSpiritVideo, handleFolderImgError, handleFolderImgLoad,
            getVideoCover, fetchBilibiliInfo, handleNewVideoCoverUpload,
            showVideoCoverUpload, closeCoverUploadModal, handleEditVideoCoverUpload,
            fetchBilibiliCoverForEdit, saveVideoCover,
            addLevel, removeLevel, copyLevelCode,
            addVideoTag, removeVideoTag, extractAllVideoTags,
            challengeSearch, challengeList, challengeDetail, searchChallenges, openChallengeDetail,
            activeFormationTab, getBattlePet, getSupportPets, isPetMarked, handleGridImgError, handleGridImgLoad,
            closeChallengeDetail, getFormationCount, getCurrentLevelStructure, getTeamIds, getFormationKeys,
            formatExtraProperty,
            downloadChallengeLongImg, downloadLongImgLoading, downloadLongImgMode, downloadLongImgProgress,
            formatPlayCount, formatSpiritName, formatTime,
            forumStats, forumCategories, forumCategory, forumSort, forumSearch,
            forumPosts, forumPage, forumTotal, forumPageSize, forumDetailPost,
            forumComments, forumCommentTotal, showPostForm,
            newPostTitle, newPostContent, newPostAuthor, newPostCategory,
            newPostImages, uploadingImage,
            newCommentContent, newCommentAuthor, newCommentImages, uploadingCommentImage,
            searchForumPosts, openForumPost, closeForumPost,
            submitPost, submitComment, likeForumPost, likeForumComment,
            handlePostImageUpload, removePostImage,
            handleCommentImageUpload, removeCommentImage,
            parseImages, deleteForumPost, deleteForumComment,
            // Auth
            currentUser, showAuthModal, authMode, authUsername, authPassword, authLoading,
            openLogin, openRegister, closeAuthModal, doLogin, doRegister, logout,
            checkCanManage, checkIsAdmin, checkIsSuperAdmin,
            // Avatar Upload
            showAvatarUpload, avatarUploading, uploadAvatar,
            // Change Password
            showPasswordModal, oldPassword, newPassword, confirmPassword, changePassword,
            // Admin Panel
            showAdminPanel, adminUsers, adminUsersPage, adminUsersTotal, adminSearchKeyword,
            loadAdminUsers, changeUserRole,
            // User Profile
            showUserProfile, profileUser, profilePosts, profileComments, currentProfileTab, openUserProfile,
            // Post Edit
            editingPost, editPostTitle, editPostContent, editPostCategory,
            startEditPost, cancelEditPost, saveEditPost,
            // Post Pin/Feature
            pinPost, featurePost,
            // Favorites
            favoritePosts, toggleFavorite, loadFavorites,
            // Comment Reply
            replyToComment, setReplyTo, cancelReply,
            // Video Like & Comments
            videoComments, newVideoComment, showVideoComments, currentVideoForComments,
            likeVideo, loadVideoComments, addVideoComment, deleteVideoComment,
            // Notifications
            notifications, unreadCount, showNotifications,
            loadNotifications, loadUnreadCount, markNotificationRead, markAllRead,
            // Audit Logs
            showAuditLogs, auditLogs, auditLogsPage, loadAuditLogs,
            // Database Backup
            backupDatabase,
            // Formation Simulator
            showFormationSim, formationSlots, formationSearchKeyword, formationSearchResults,
            addToFormation, removeFromFormation, handleDragStart, handleDrop,
            debounceSearchForFormation, searchPetsForFormation, clearFormation,
            getSimFormationCount, getFormationElements,
            // Lightbox
            lightboxImages, lightboxIndex, showLightbox,
            openLightbox, closeLightbox, prevLightbox, nextLightbox,
            // Skeleton Loading
            loadingPets, loadingForum, loadingVideos,
            // Page Transition
            pageTransitionName,
            // Dark Mode
            darkMode, toggleDarkMode,
            // Report
            showReportModal, reportTarget, reportReason, openReport, submitReport,
        };
    }
});

app.mount('#app');
