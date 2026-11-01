/**
 * MK Graphy admin panel.
 * All data is read from and written to the local Python/SQLite backend.
 */
(() => {
  'use strict';

  const escapeHtml = (value) =>
    String(value ?? '').replace(/[&<>"']/g, (character) => ({
      '&': '&amp;',
      '<': '&lt;',
      '>': '&gt;',
      '"': '&quot;',
      "'": '&#39;',
    }[character]));

  const toFa = (value) =>
    String(value ?? '').replace(/\d/g, (digit) => '۰۱۲۳۴۵۶۷۸۹'[Number(digit)]);

  const qs = (selector, root = document) => root.querySelector(selector);
  const qsa = (selector, root = document) => Array.from(root.querySelectorAll(selector));

  const STATUS_LABELS = {
    pending: 'در انتظار',
    contacted: 'تماس گرفته‌شده',
    closed: 'بسته‌شده',
  };
  const URGENCY_LABELS = {
    immediate: 'فوری',
    today: 'امروز',
    this_week: 'این هفته',
    normal: 'عادی',
  };
  const TYPE_LABELS = {
    web: 'توسعه وب',
    video: 'تولید ویدیو',
  };
  const PANEL_TITLES = {
    overview: ['داشبورد', 'خلاصه وضعیت استودیو و سفارش‌های اخیر'],
    orders: ['سفارش‌های تماس', 'مدیریت و پیگیری درخواست‌های کاربران'],
    projects: ['نمونه‌کارها', 'افزودن، ویرایش و انتشار پروژه‌های استودیو'],
    users: ['کاربران', 'حساب‌های ثبت‌شده و دسترسی‌های مدیریتی'],
    settings: ['تنظیمات ادمین', 'اطلاعات حساب و رمز عبور مدیر'],
  };

  const state = {
    session: null,
    projects: [],
    orders: [],
    users: [],
    stats: {},
    recentOrders: [],
    orderFilter: '',
    activeTab: 'overview',
    editingId: null,
    imagePaths: { image: '', banner: '', gallery: [] },
    imageFiles: { image: null, banner: null, gallery0: null, gallery1: null, gallery2: null },
    previewUrls: [],
    toastTimer: null,
  };

  const projectModal = qs('#project-modal');
  const projectForm = qs('[data-project-form]');
  const projectFormError = qs('[data-project-form-error]');
  const projectSubmit = qs('[data-project-submit]');
  const ordersList = qs('[data-orders-list]');
  const projectsList = qs('[data-projects-list]');
  const usersList = qs('[data-users-list]');
  const recentOrdersBox = qs('[data-recent-orders]');
  const orderFilter = qs('[data-order-filter]');
  const globalError = qs('[data-admin-global-error]');
  const profileForm = qs('[data-admin-profile-form]');
  const profileError = qs('[data-profile-error]');
  const profileSuccess = qs('[data-profile-success]');
  const profileSubmit = qs('[data-profile-submit]');
  const toast = qs('[data-admin-toast]');

  /* ----------------------------------------------------------------------
     Small UI helpers
     ---------------------------------------------------------------------- */

  function showToast(message, type = 'success') {
    if (!toast) return;
    toast.textContent = message;
    toast.classList.toggle('is-error', type === 'error');
    toast.hidden = false;
    if (state.toastTimer) window.clearTimeout(state.toastTimer);
    state.toastTimer = window.setTimeout(() => {
      toast.hidden = true;
    }, 4200);
  }

  function showGlobalError(message) {
    if (!globalError) return;
    globalError.textContent = message;
    globalError.hidden = !message;
  }

  function formatDate(value) {
    if (!value) return '—';
    try {
      return new Intl.DateTimeFormat('fa-IR', {
        dateStyle: 'medium',
        timeStyle: 'short',
      }).format(new Date(value));
    } catch {
      return value;
    }
  }

  function handleAuthError(error) {
    if (error?.status === 401) {
      window.location.replace('/admin/login');
      return true;
    }
    if (error?.status === 403) {
      // The session exists but the account is not (or no longer) an admin.
      // The server refuses to render /admin or /admin/login for such accounts,
      // so send the visitor back to the public site instead of a dead end.
      window.location.replace('/');
      return true;
    }
    return false;
  }

  function renderPasswordBanner() {
    const banner = qs('[data-password-banner]');
    if (!banner) return;
    banner.hidden = !state.session?.user?.mustChangePassword;
  }

  function renderAdminName() {
    const name = state.session?.user
      ? `${state.session.user.firstName} ${state.session.user.lastName}`
      : 'مدیر';
    qsa('[data-admin-name]').forEach((element) => {
      element.textContent = name;
    });
  }

  /* ----------------------------------------------------------------------
     Loaders
     ---------------------------------------------------------------------- */

  async function loadOverview() {
    try {
      const data = await window.MKApi.get('/api/admin/overview');
      state.stats = data.stats || {};
      state.recentOrders = data.recentOrders || [];
      renderStats();
      renderRecentOrders();
    } catch (error) {
      if (!handleAuthError(error)) showGlobalError(error.message);
    }
  }

  async function loadOrders() {
    try {
      const query = state.orderFilter ? `?status=${encodeURIComponent(state.orderFilter)}` : '';
      const data = await window.MKApi.get(`/api/admin/orders${query}`);
      state.orders = data.orders || [];
      renderOrders();
    } catch (error) {
      if (!handleAuthError(error)) showGlobalError(error.message);
    }
  }

  async function loadProjects() {
    try {
      const data = await window.MKApi.get('/api/admin/projects');
      state.projects = data.projects || [];
      renderProjects();
    } catch (error) {
      if (!handleAuthError(error)) showGlobalError(error.message);
    }
  }

  async function loadUsers() {
    try {
      const data = await window.MKApi.get('/api/admin/users');
      state.users = data.users || [];
      renderUsers();
    } catch (error) {
      if (!handleAuthError(error)) showGlobalError(error.message);
    }
  }

  /* ----------------------------------------------------------------------
     Renderers
     ---------------------------------------------------------------------- */

  function renderStats() {
    qsa('[data-stat]').forEach((element) => {
      const key = element.dataset.stat;
      element.textContent = toFa(state.stats[key] ?? 0);
    });
    const badge = qs('[data-pending-badge]');
    const pending = Number(state.stats.pendingOrders || 0);
    if (badge) {
      badge.textContent = toFa(pending);
      badge.hidden = pending === 0;
    }
  }

  function statusBadge(status) {
    return `<span class="admin-status-badge is-${escapeHtml(status)}">${escapeHtml(STATUS_LABELS[status] || status)}</span>`;
  }

  function urgencyBadge(urgency) {
    return `<span class="admin-urgency admin-urgency--${escapeHtml(urgency)}">${escapeHtml(URGENCY_LABELS[urgency] || urgency)}</span>`;
  }

  function renderRecentOrders() {
    if (!recentOrdersBox) return;
    if (!state.recentOrders.length) {
      recentOrdersBox.innerHTML = '<div class="admin-empty">هنوز سفارش تماسی ثبت نشده است.</div>';
      return;
    }
    recentOrdersBox.innerHTML = state.recentOrders
      .map(
        (order) => `
          <article class="admin-recent-item">
            <div class="admin-recent-head">
              <div>
                <strong>${escapeHtml(order.firstName)} ${escapeHtml(order.lastName)}</strong>
                <span dir="ltr">${escapeHtml(order.phone)}</span>
              </div>
              ${statusBadge(order.status)}
            </div>
            <div class="admin-recent-meta">
              ${urgencyBadge(order.urgency)}
              <time>${escapeHtml(formatDate(order.createdAt))}</time>
            </div>
            <p>${escapeHtml(order.reason)}</p>
          </article>
        `,
      )
      .join('');
  }

  function renderOrders() {
    if (!ordersList) return;
    if (!state.orders.length) {
      ordersList.innerHTML = '<div class="admin-empty">سفارشی با این وضعیت وجود ندارد.</div>';
      return;
    }
    ordersList.innerHTML = state.orders
      .map(
        (order) => `
          <article class="admin-order-card" data-order-card data-order-id="${order.id}">
            <div class="admin-order-head">
              <div class="admin-order-person">
                <span class="admin-avatar" aria-hidden="true">${escapeHtml((order.firstName || 'م').slice(0, 1))}</span>
                <div>
                  <strong>${escapeHtml(order.firstName)} ${escapeHtml(order.lastName)}</strong>
                  <span dir="ltr">${escapeHtml(order.phone)}</span>
                </div>
              </div>
              <div class="admin-order-head-meta">
                ${statusBadge(order.status)}
                <time>${escapeHtml(formatDate(order.createdAt))}</time>
              </div>
            </div>

            <div class="admin-order-body">
              <div class="admin-order-label">فوریت درخواست: ${urgencyBadge(order.urgency)}</div>
              <p>${escapeHtml(order.reason)}</p>
            </div>

            <div class="admin-order-controls">
              <label>
                <span>وضعیت</span>
                <select class="admin-input admin-input--compact" data-order-status>
                  ${Object.entries(STATUS_LABELS)
                    .map(
                      ([value, label]) =>
                        `<option value="${value}" ${order.status === value ? 'selected' : ''}>${label}</option>`,
                    )
                    .join('')}
                </select>
              </label>
              <label class="admin-order-note-field">
                <span>یادداشت مدیر</span>
                <input class="admin-input" data-order-note type="text" maxlength="1000"
                  value="${escapeHtml(order.adminNote || '')}" placeholder="نتیجه پیگیری یا توضیح داخلی">
              </label>
              <button class="btn btn-primary btn-sm" type="button" data-order-save>ذخیره</button>
            </div>
          </article>
        `,
      )
      .join('');
  }

  function renderProjects() {
    if (!projectsList) return;
    if (!state.projects.length) {
      projectsList.innerHTML = '<div class="admin-empty">هنوز نمونه‌کاری ثبت نشده است.</div>';
      return;
    }
    projectsList.innerHTML = state.projects
      .map(
        (project) => `
          <article class="admin-project-row" data-project-row data-project-id="${project.id}">
            <div class="admin-project-thumb">
              ${project.image ? `<img src="${escapeHtml(project.image)}" alt="${escapeHtml(project.title)}" loading="lazy">` : ''}
            </div>
            <div class="admin-project-info">
              <strong>${escapeHtml(project.title)}</strong>
              <span dir="ltr">${escapeHtml(project.slug)}</span>
              <div class="admin-project-tags">
                <span class="admin-type-badge is-${escapeHtml(project.type)}">${escapeHtml(TYPE_LABELS[project.type] || project.type)}</span>
                <span class="admin-status-badge ${project.isPublished ? 'is-published' : 'is-draft'}">
                  ${project.isPublished ? 'منتشرشده' : 'پیش‌نویس'}
                </span>
              </div>
            </div>
            <div class="admin-project-order">
              <span>ترتیب</span>
              <strong>${toFa(project.sortOrder)}</strong>
            </div>
            <div class="admin-project-actions">
              <button class="btn btn-secondary btn-sm" type="button" data-project-edit="${project.id}">ویرایش</button>
              <button class="admin-link-btn admin-link-btn--danger" type="button" data-project-delete="${project.id}">حذف</button>
            </div>
          </article>
        `,
      )
      .join('');
  }

  function renderUsers() {
    if (!usersList) return;
    if (!state.users.length) {
      usersList.innerHTML = '<div class="admin-empty">کاربری ثبت نشده است.</div>';
      return;
    }
    usersList.innerHTML = state.users
      .map(
        (user) => {
          const isSelf = user.id === state.session?.user?.id;
          const roleButton = isSelf
            ? '<span class="admin-muted">حساب شما</span>'
            : user.role === 'admin'
              ? `<button class="admin-link-btn admin-link-btn--danger" type="button" data-user-role="${user.id}" data-role="user">حذف دسترسی مدیریت</button>`
              : `<button class="admin-link-btn" type="button" data-user-role="${user.id}" data-role="admin">ارتقا به مدیر</button>`;
          return `
            <article class="admin-user-row">
              <span class="admin-avatar" aria-hidden="true">${escapeHtml((user.firstName || 'ک').slice(0, 1))}</span>
              <div class="admin-user-info">
                <strong>${escapeHtml(user.firstName)} ${escapeHtml(user.lastName)}</strong>
                <span dir="ltr">${escapeHtml(user.phone)}</span>
                <span class="admin-user-date">ثبت‌نام: ${escapeHtml(formatDate(user.createdAt))}</span>
              </div>
              <div class="admin-user-meta">
                <span class="admin-role-badge is-${escapeHtml(user.role)}">${user.role === 'admin' ? 'مدیر' : 'کاربر'}</span>
                <span class="admin-user-orders">${toFa(user.orderCount)} سفارش</span>
              </div>
              <div class="admin-user-action">${roleButton}</div>
            </article>
          `;
        },
      )
      .join('');
  }

  /* ----------------------------------------------------------------------
     Tabs and profile
     ---------------------------------------------------------------------- */

  function activateTab(name) {
    if (!PANEL_TITLES[name]) return;
    state.activeTab = name;
    qsa('[data-admin-panel]').forEach((panel) => {
      panel.hidden = panel.dataset.adminPanel !== name;
    });
    qsa('.admin-nav-btn').forEach((button) => {
      button.classList.toggle('is-active', button.dataset.adminTab === name);
    });
    const [title, subtitle] = PANEL_TITLES[name];
    const titleElement = qs('[data-panel-title]');
    const subtitleElement = qs('[data-panel-subtitle]');
    if (titleElement) titleElement.textContent = title;
    if (subtitleElement) subtitleElement.textContent = subtitle;
    window.scrollTo({ top: 0, behavior: 'smooth' });
  }

  function fillProfileForm() {
    if (!profileForm || !state.session?.user) return;
    const user = state.session.user;
    profileForm.elements.namedItem('firstName').value = user.firstName || '';
    profileForm.elements.namedItem('lastName').value = user.lastName || '';
    profileForm.elements.namedItem('phone').value = user.phone || '';
    profileForm.elements.namedItem('currentPassword').value = '';
    profileForm.elements.namedItem('newPassword').value = '';
    profileForm.elements.namedItem('newPasswordConfirm').value = '';
    if (profileError) profileError.hidden = true;
    if (profileSuccess) profileSuccess.hidden = true;
  }

  async function handleProfileSubmit(event) {
    event.preventDefault();
    if (profileError) profileError.hidden = true;
    if (profileSuccess) profileSuccess.hidden = true;

    const value = (name) => profileForm.elements.namedItem(name)?.value.trim() || '';
    const currentPassword = profileForm.elements.namedItem('currentPassword')?.value || '';
    const newPassword = profileForm.elements.namedItem('newPassword')?.value || '';
    const newPasswordConfirm = profileForm.elements.namedItem('newPasswordConfirm')?.value || '';

    if (!currentPassword) {
      if (profileError) {
        profileError.textContent = 'برای ذخیره تغییرات، رمز عبور فعلی را وارد کنید.';
        profileError.hidden = false;
      }
      return;
    }
    if (newPassword && newPassword !== newPasswordConfirm) {
      if (profileError) {
        profileError.textContent = 'تکرار رمز عبور جدید یکسان نیست.';
        profileError.hidden = false;
      }
      return;
    }

    profileSubmit.disabled = true;
    try {
      const payload = await window.MKApi.put('/api/admin/profile', {
        firstName: value('firstName'),
        lastName: value('lastName'),
        phone: value('phone'),
        currentPassword,
        newPassword,
        newPasswordConfirm,
      });
      state.session.user = payload.user;
      renderAdminName();
      fillProfileForm();
      renderPasswordBanner();
      if (profileSuccess) {
        profileSuccess.textContent = payload.message || 'تنظیمات با موفقیت ذخیره شد.';
        profileSuccess.hidden = false;
      }
      showToast('تنظیمات حساب مدیر ذخیره شد.');
    } catch (error) {
      if (!handleAuthError(error) && profileError) {
        profileError.textContent = error.message;
        profileError.hidden = false;
      }
    } finally {
      profileSubmit.disabled = false;
    }
  }

  /* ----------------------------------------------------------------------
     Project modal
     ---------------------------------------------------------------------- */

  function nextSortOrder() {
    const max = state.projects.reduce((current, project) => Math.max(current, Number(project.sortOrder) || 0), 0);
    return max + 10;
  }

  function revokePreviewUrls() {
    state.previewUrls.forEach((url) => URL.revokeObjectURL(url));
    state.previewUrls = [];
  }

  function galleryIndexForKey(key) {
    return key.startsWith('gallery') ? Number(key.slice('gallery'.length)) : null;
  }

  function setImagePath(key, value) {
    if (key === 'image' || key === 'banner') {
      state.imagePaths[key] = value;
      return;
    }
    const index = galleryIndexForKey(key);
    if (index !== null) state.imagePaths.gallery[index] = value;
  }

  function getImagePath(key) {
    if (key === 'image' || key === 'banner') return state.imagePaths[key] || '';
    const index = galleryIndexForKey(key);
    return index === null ? '' : state.imagePaths.gallery[index] || '';
  }

  function renderImagePreviews() {
    revokePreviewUrls();
    qsa('[data-image-preview]').forEach((preview) => {
      preview.innerHTML = '';
      preview.hidden = true;
    });

    ['image', 'banner', 'gallery0', 'gallery1', 'gallery2'].forEach((key) => {
      const preview = qs(`[data-image-preview="${key}"]`);
      if (!preview) return;
      let source = '';
      const file = state.imageFiles[key];
      if (file) {
        source = URL.createObjectURL(file);
        state.previewUrls.push(source);
      } else {
        source = getImagePath(key);
      }
      if (!source) return;
      preview.innerHTML = `<img src="${escapeHtml(source)}" alt="پیش‌نمایش تصویر" loading="lazy">`;
      preview.hidden = false;
    });
  }

  function openProjectModal(project = null) {
    if (!projectModal || !projectForm) return;
    state.editingId = project?.id || null;
    state.imagePaths = {
      image: project?.image || '',
      banner: project?.bannerImage || '',
      gallery: Array.isArray(project?.gallery) ? [...project.gallery] : [],
    };
    state.imageFiles = { image: null, banner: null, gallery0: null, gallery1: null, gallery2: null };
    revokePreviewUrls();

    projectForm.reset();
    qsa('[data-image-input]').forEach((input) => {
      input.value = '';
    });

    const setValue = (name, value) => {
      const field = projectForm.elements.namedItem(name);
      if (field) field.value = value ?? '';
    };

    setValue('slug', project?.slug || '');
    projectForm.elements.namedItem('type').value = project?.type || 'web';
    setValue('categoryName', project?.categoryName || '');
    setValue('title', project?.title || '');
    setValue('subtitle', project?.subtitle || '');
    setValue('shortDescription', project?.shortDescription || '');
    setValue('fullDescription', project?.fullDescription || '');
    setValue('client', project?.client || '');
    setValue('year', project?.year || '');
    setValue('duration', project?.duration || '');
    setValue('servicesProvided', (project?.servicesProvided || []).join('\n'));
    setValue('technologies', (project?.technologies || []).join('\n'));
    setValue('challenge', project?.challenge || '');
    setValue('solution', project?.solution || '');
    setValue('testimonial', project?.testimonial || '');
    setValue('sortOrder', project?.sortOrder ?? nextSortOrder());
    projectForm.elements.namedItem('isPublished').checked = project ? project.isPublished : true;

    const statLabels = qsa('[name="statLabel"]', projectForm);
    const statValues = qsa('[name="statValue"]', projectForm);
    statLabels.forEach((input, index) => {
      input.value = project?.stats?.[index]?.label || '';
    });
    statValues.forEach((input, index) => {
      input.value = project?.stats?.[index]?.value || '';
    });

    if (projectFormError) projectFormError.hidden = true;
    const title = qs('[data-project-modal-title]');
    if (title) title.textContent = project ? 'ویرایش نمونه‌کار' : 'افزودن نمونه‌کار';
    if (projectSubmit) projectSubmit.textContent = project ? 'ذخیره تغییرات' : 'ایجاد نمونه‌کار';

    renderImagePreviews();
    projectModal.hidden = false;
    document.body.classList.add('is-locked');
    const card = qs('.admin-modal-card', projectModal);
    if (card) card.scrollTop = 0;
    window.requestAnimationFrame(() => {
      qs('#project-slug')?.focus();
    });
  }

  function closeProjectModal() {
    if (!projectModal || projectModal.hidden) return;
    projectModal.hidden = true;
    document.body.classList.remove('is-locked');
    revokePreviewUrls();
    state.editingId = null;
  }

  function showProjectError(message) {
    if (!projectFormError) return;
    projectFormError.textContent = message;
    projectFormError.hidden = !message;
    projectFormError.scrollIntoView({ behavior: 'smooth', block: 'nearest' });
  }

  function handleImageInput(event) {
    const input = event.currentTarget;
    const key = input.dataset.imageInput;
    if (!key) return;
    const file = input.files?.[0] || null;
    if (!file) {
      state.imageFiles[key] = null;
      renderImagePreviews();
      return;
    }
    const allowed = ['image/jpeg', 'image/png', 'image/webp'];
    if (file.type && !allowed.includes(file.type)) {
      showProjectError('فقط تصاویر JPEG، PNG یا WebP مجاز هستند.');
      input.value = '';
      state.imageFiles[key] = null;
      renderImagePreviews();
      return;
    }
    if (file.size > 12 * 1024 * 1024) {
      showProjectError('حجم فایل انتخاب‌شده بیش از حد مجاز است.');
      input.value = '';
      state.imageFiles[key] = null;
      renderImagePreviews();
      return;
    }
    if (projectFormError) projectFormError.hidden = true;
    state.imageFiles[key] = file;
    renderImagePreviews();
  }

  function clearImage(key) {
    state.imageFiles[key] = null;
    const input = qs(`[data-image-input="${key}"]`);
    if (input) input.value = '';
    setImagePath(key, '');
    renderImagePreviews();
  }

  async function compressImage(file) {
    if (file.size > 12 * 1024 * 1024) {
      throw new Error('حجم هر تصویر باید کمتر از ۱۲ مگابایت باشد.');
    }
    const dataUrl = await new Promise((resolve, reject) => {
      const reader = new FileReader();
      reader.onload = () => resolve(reader.result);
      reader.onerror = () => reject(new Error('خواندن فایل تصویر ممکن نشد.'));
      reader.readAsDataURL(file);
    });
    try {
      const image = await new Promise((resolve, reject) => {
        const element = new Image();
        element.onload = () => resolve(element);
        element.onerror = () => reject(new Error('خواندن تصویر ممکن نشد.'));
        element.src = dataUrl;
      });
      const maxDimension = 1600;
      const scale = Math.min(1, maxDimension / Math.max(image.naturalWidth, image.naturalHeight));
      const width = Math.max(1, Math.round(image.naturalWidth * scale));
      const height = Math.max(1, Math.round(image.naturalHeight * scale));
      const canvas = document.createElement('canvas');
      canvas.width = width;
      canvas.height = height;
      const context = canvas.getContext('2d');
      if (!context) throw new Error('پردازش تصویر در مرورگر ممکن نشد.');
      context.drawImage(image, 0, 0, width, height);
      let output = canvas.toDataURL('image/webp', 0.82);
      if (!output.startsWith('data:image/webp')) {
        output = canvas.toDataURL('image/jpeg', 0.85);
      }
      if (!output || output.length > 8 * 1024 * 1024) {
        throw new Error('تصویر پس از فشرده‌سازی هنوز بیش از حد بزرگ است.');
      }
      return output;
    } catch (error) {
      throw error;
    }
  }

  async function resolveImageValue(key) {
    const file = state.imageFiles[key];
    if (file) return compressImage(file);
    return getImagePath(key);
  }

  async function buildProjectPayload() {
    const value = (name) => projectForm.elements.namedItem(name)?.value.trim() || '';
    const services = value('servicesProvided')
      .split(/\n+/)
      .map((item) => item.trim())
      .filter(Boolean);
    const technologies = value('technologies')
      .split(/[\n,،]+/)
      .map((item) => item.trim())
      .filter(Boolean);

    const statLabels = qsa('[name="statLabel"]', projectForm);
    const statValues = qsa('[name="statValue"]', projectForm);
    const stats = [];
    statLabels.forEach((input, index) => {
      const label = input.value.trim();
      const valueElement = statValues[index];
      const statValue = valueElement?.value.trim() || '';
      if (label && statValue) stats.push({ label, value: statValue });
    });

    if (!services.length) throw new Error('حداقل یک خدمت ارائه‌شده وارد کنید.');
    if (!stats.length) throw new Error('حداقل یک شاخص آماری وارد کنید.');

    const image = await resolveImageValue('image');
    if (!image) throw new Error('تصویر کاور را انتخاب کنید.');
    const banner = (await resolveImageValue('banner')) || image;
    const gallery = [];
    for (let index = 0; index < 3; index += 1) {
      const imageValue = await resolveImageValue(`gallery${index}`);
      if (imageValue) gallery.push(imageValue);
    }
    if (!gallery.length) throw new Error('حداقل یک تصویر گالری انتخاب کنید.');

    return {
      slug: value('slug').toLowerCase(),
      type: value('type'),
      categoryName: value('categoryName'),
      title: value('title'),
      subtitle: value('subtitle'),
      shortDescription: value('shortDescription'),
      fullDescription: value('fullDescription'),
      client: value('client'),
      year: value('year'),
      duration: value('duration'),
      servicesProvided: services,
      technologies,
      image,
      bannerImage: banner,
      gallery,
      stats,
      challenge: value('challenge'),
      solution: value('solution'),
      testimonial: value('testimonial'),
      sortOrder: Number(value('sortOrder') || nextSortOrder()),
      isPublished: Boolean(projectForm.elements.namedItem('isPublished').checked),
    };
  }

  async function handleProjectSubmit(event) {
    event.preventDefault();
    if (projectFormError) projectFormError.hidden = true;
    const originalText = projectSubmit.textContent;
    projectSubmit.disabled = true;
    projectSubmit.textContent = 'در حال ذخیره…';
    try {
      const payload = await buildProjectPayload();
      if (state.editingId) {
        await window.MKApi.put(`/api/admin/projects/${state.editingId}`, payload);
        showToast('نمونه‌کار با موفقیت ویرایش شد.');
      } else {
        await window.MKApi.post('/api/admin/projects', payload);
        showToast('نمونه‌کار جدید ایجاد شد.');
      }
      closeProjectModal();
      await Promise.all([loadProjects(), loadOverview()]);
      activateTab('projects');
    } catch (error) {
      if (!handleAuthError(error)) showProjectError(error.message);
    } finally {
      projectSubmit.disabled = false;
      projectSubmit.textContent = originalText;
    }
  }

  /* ----------------------------------------------------------------------
     Orders, users, deletion and logout
     ---------------------------------------------------------------------- */

  async function saveOrder(button) {
    const card = button.closest('[data-order-card]');
    if (!card) return;
    const orderId = card.dataset.orderId;
    const status = qs('[data-order-status]', card)?.value || 'pending';
    const note = qs('[data-order-note]', card)?.value || '';
    button.disabled = true;
    try {
      await window.MKApi.patch(`/api/admin/orders/${orderId}`, { status, adminNote: note });
      showToast('وضعیت سفارش به‌روزرسانی شد.');
      await Promise.all([loadOrders(), loadOverview()]);
    } catch (error) {
      if (!handleAuthError(error)) showToast(error.message, 'error');
    } finally {
      button.disabled = false;
    }
  }

  async function deleteProject(projectId) {
    const project = state.projects.find((item) => item.id === Number(projectId));
    if (!project) return;
    if (!window.confirm(`نمونه‌کار «${project.title}» حذف شود؟ این کار قابل بازگشت نیست.`)) return;
    try {
      await window.MKApi.del(`/api/admin/projects/${projectId}`);
      showToast('نمونه‌کار حذف شد.');
      await Promise.all([loadProjects(), loadOverview()]);
    } catch (error) {
      if (!handleAuthError(error)) showToast(error.message, 'error');
    }
  }

  async function updateUserRole(button) {
    const userId = button.dataset.userRole;
    const role = button.dataset.role;
    const user = state.users.find((item) => item.id === Number(userId));
    if (!user) return;
    const question =
      role === 'admin'
        ? `دسترسی مدیریت برای «${user.firstName} ${user.lastName}» فعال شود؟`
        : `دسترسی مدیریت «${user.firstName} ${user.lastName}» حذف شود؟`;
    if (!window.confirm(question)) return;
    button.disabled = true;
    try {
      await window.MKApi.patch(`/api/admin/users/${userId}`, { role });
      showToast('نقش کاربر تغییر کرد.');
      await Promise.all([loadUsers(), loadOverview()]);
    } catch (error) {
      if (!handleAuthError(error)) showToast(error.message, 'error');
      button.disabled = false;
    }
  }

  async function logout() {
    try {
      await window.MKApi.post('/api/auth/logout', {});
    } catch {
      // Redirect regardless; the cookie is cleared server-side when possible.
    }
    window.location.replace('/admin/login');
  }

  /* ----------------------------------------------------------------------
     Init and events
     ---------------------------------------------------------------------- */

  function bindEvents() {
    document.addEventListener('click', (event) => {
      const target = event.target;
      if (!(target instanceof Element)) return;

      const tabButton = target.closest('[data-admin-tab]');
      if (tabButton) {
        activateTab(tabButton.dataset.adminTab);
        return;
      }
      if (target.closest('[data-admin-logout]')) {
        logout();
        return;
      }
      if (target.closest('[data-project-new]')) {
        openProjectModal();
        return;
      }
      const editButton = target.closest('[data-project-edit]');
      if (editButton) {
        const project = state.projects.find((item) => item.id === Number(editButton.dataset.projectEdit));
        if (project) openProjectModal(project);
        return;
      }
      const deleteButton = target.closest('[data-project-delete]');
      if (deleteButton) {
        deleteProject(deleteButton.dataset.projectDelete);
        return;
      }
      const roleButton = target.closest('[data-user-role]');
      if (roleButton) {
        updateUserRole(roleButton);
        return;
      }
      const orderButton = target.closest('[data-order-save]');
      if (orderButton) {
        saveOrder(orderButton);
        return;
      }
      if (target.closest('[data-project-close]')) {
        closeProjectModal();
        return;
      }
      const clearButton = target.closest('[data-clear-image]');
      if (clearButton) {
        clearImage(clearButton.dataset.clearImage);
      }
    });

    qsa('[data-image-input]').forEach((input) => {
      input.addEventListener('change', handleImageInput);
    });

    orderFilter?.addEventListener('change', () => {
      state.orderFilter = orderFilter.value;
      loadOrders();
    });

    projectForm?.addEventListener('submit', handleProjectSubmit);
    profileForm?.addEventListener('submit', handleProfileSubmit);

    document.addEventListener('keydown', (event) => {
      if (event.key === 'Escape' && projectModal && !projectModal.hidden) {
        closeProjectModal();
      }
    });

    window.addEventListener('mk:unauthorized', () => {
      window.location.replace('/admin/login');
    });
  }

  async function init() {
    bindEvents();
    try {
      const session = await window.MKApi.get('/api/session');
      if (!session?.user || session.user.role !== 'admin') {
        window.location.replace('/admin/login');
        return;
      }
      state.session = session;
      window.MKApi.setCsrf(session.csrfToken);
      renderAdminName();
      renderPasswordBanner();
      fillProfileForm();
      await Promise.all([loadOverview(), loadOrders(), loadProjects(), loadUsers()]);
      activateTab('overview');
    } catch (error) {
      if (!handleAuthError(error)) {
        showGlobalError(error.message || 'بارگذاری پنل مدیریت انجام نشد.');
      }
    }
  }

  init();
})();
