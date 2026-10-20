import { app } from '../../scripts/app.js'
import { api } from '../../scripts/api.js'
import { isAnimImageInputClass } from './input_contract.js'
import { startPushBridge } from './push_bridge.js'

function makeId() {
  return globalThis.crypto?.randomUUID?.() || `anim-${Date.now().toString(36)}-${Math.random().toString(36).slice(2)}`
}

const sessionId = sessionStorage.getItem('anim-bridge-session') || makeId()
const tabId = makeId()
sessionStorage.setItem('anim-bridge-session', sessionId)
sessionStorage.setItem('anim-bridge-tab', tabId)

function splitImagePaths(value) {
  return String(value || '').split(/\r?\n/).map((item) => item.trim()).filter(Boolean)
}

function inputPreviewUrl(fileName) {
  const query = new URLSearchParams({ filename: fileName, type: 'input' })
  return api.apiURL(`/view?${query}`)
}

async function uploadInputImage(file) {
  const body = new FormData()
  body.append('image', file, file.name)
  body.append('type', 'input')
  body.append('overwrite', 'false')
  const response = await api.fetchApi('/upload/image', { method: 'POST', body })
  if (!response.ok) throw new Error(`Upload failed (${response.status})`)
  const result = await response.json()
  if (!result?.name) throw new Error('ComfyUI did not return an uploaded file name.')
  return result.subfolder ? `${result.subfolder}/${result.name}` : result.name
}

function setupImageReferenceBoard(node) {
  const pathsWidget = node.widgets?.find((widget) => widget.name === 'image_paths')
  const capacityWidget = node.widgets?.find((widget) => widget.name === 'max_references')
  if (!pathsWidget || node.__animImageBoard) return
  node.__animImageBoard = true
  pathsWidget.type = 'hidden'
  pathsWidget.computeSize = () => [0, -4]

  const container = document.createElement('div')
  Object.assign(container.style, {
    boxSizing: 'border-box',
    minHeight: '220px',
    padding: '10px',
    borderRadius: '10px',
    background: 'rgba(10, 14, 18, 0.88)',
    color: '#d9f7df',
    fontFamily: 'system-ui, sans-serif',
  })
  const toolbar = document.createElement('div')
  Object.assign(toolbar.style, { display: 'flex', gap: '8px', alignItems: 'center' })
  const uploadButton = document.createElement('button')
  uploadButton.textContent = 'Upload images'
  const clearButton = document.createElement('button')
  clearButton.textContent = 'Clear'
  const count = document.createElement('span')
  Object.assign(count.style, { marginLeft: 'auto', fontWeight: '700' })
  const hint = document.createElement('div')
  hint.textContent = 'Drag cards to set the reference order used by Anim.'
  Object.assign(hint.style, { margin: '8px 0', fontSize: '12px', opacity: '0.8' })
  const grid = document.createElement('div')
  Object.assign(grid.style, {
    display: 'grid',
    gridTemplateColumns: 'repeat(auto-fill, minmax(82px, 1fr))',
    gap: '8px',
  })
  const picker = document.createElement('input')
  picker.type = 'file'
  picker.accept = 'image/*'
  picker.multiple = true
  picker.style.display = 'none'
  toolbar.append(uploadButton, clearButton, count)
  container.append(toolbar, hint, grid, picker)

  const getCapacity = () => Math.max(1, Number(capacityWidget?.value || 1))
  const getPaths = () => splitImagePaths(pathsWidget.value)
  const setPaths = (paths) => {
    pathsWidget.value = paths.join('\n')
    pathsWidget.callback?.(pathsWidget.value)
    node.setDirtyCanvas?.(true, true)
    render()
  }
  let draggedIndex = -1
  const render = () => {
    const paths = getPaths()
    count.textContent = `${paths.length}/${getCapacity()} images`
    grid.replaceChildren()
    for (const [index, fileName] of paths.entries()) {
      const card = document.createElement('div')
      card.draggable = true
      Object.assign(card.style, {
        position: 'relative',
        minHeight: '92px',
        border: '1px solid #5f8f69',
        borderRadius: '8px',
        overflow: 'hidden',
        background: '#151b18',
        cursor: 'grab',
      })
      const image = document.createElement('img')
      image.src = inputPreviewUrl(fileName)
      image.alt = fileName
      Object.assign(image.style, { width: '100%', height: '92px', objectFit: 'contain' })
      const order = document.createElement('span')
      order.textContent = String(index + 1)
      Object.assign(order.style, {
        position: 'absolute', left: '5px', bottom: '5px', padding: '1px 5px',
        borderRadius: '10px', background: 'rgba(0,0,0,.75)', color: 'white',
      })
      const remove = document.createElement('button')
      remove.textContent = '×'
      remove.title = `Remove ${fileName}`
      Object.assign(remove.style, { position: 'absolute', right: '4px', top: '4px' })
      remove.onclick = (event) => {
        event.stopPropagation()
        setPaths(paths.filter((_, itemIndex) => itemIndex !== index))
      }
      card.ondragstart = () => { draggedIndex = index }
      card.ondragover = (event) => event.preventDefault()
      card.ondrop = (event) => {
        event.preventDefault()
        if (draggedIndex < 0 || draggedIndex === index) return
        const reordered = [...paths]
        const [moved] = reordered.splice(draggedIndex, 1)
        reordered.splice(index, 0, moved)
        draggedIndex = -1
        setPaths(reordered)
      }
      card.append(image, order, remove)
      grid.append(card)
    }
  }

  const previousPathsCallback = pathsWidget.callback
  pathsWidget.callback = (...args) => {
    previousPathsCallback?.(...args)
    render()
  }

  uploadButton.onclick = () => picker.click()
  clearButton.onclick = () => setPaths([])
  picker.onchange = async () => {
    const files = Array.from(picker.files || [])
    picker.value = ''
    const available = Math.max(0, getCapacity() - getPaths().length)
    if (files.length > available) {
      globalThis.alert?.(`This node can add ${available} more image(s).`)
    }
    const uploaded = []
    for (const file of files.slice(0, available)) {
      try {
        uploaded.push(await uploadInputImage(file))
      } catch (error) {
        globalThis.alert?.(String(error))
        break
      }
    }
    if (uploaded.length) setPaths([...getPaths(), ...uploaded])
  }
  const previousCapacityCallback = capacityWidget?.callback
  if (capacityWidget) {
    capacityWidget.callback = (...args) => {
      previousCapacityCallback?.(...args)
      render()
    }
  }
  node.addDOMWidget('anim_image_reference_board', 'div', container, {
    serialize: false,
    hideOnZoom: false,
  })
  node.setSize?.([Math.max(node.size?.[0] || 0, 420), Math.max(node.size?.[1] || 0, 360)])
  render()
}

function setupImageInput(node) {
  const imageWidget = node.widgets?.find((widget) => widget.name === 'image')
  if (!imageWidget || node.__animImageInput) return
  node.__animImageInput = true

  const container = document.createElement('div')
  Object.assign(container.style, {
    boxSizing: 'border-box',
    padding: '8px',
    borderRadius: '10px',
    background: 'rgba(10, 14, 18, 0.88)',
    color: '#d9f7df',
    fontFamily: 'system-ui, sans-serif',
  })
  const uploadButton = document.createElement('button')
  uploadButton.textContent = 'Upload image'
  const clearButton = document.createElement('button')
  clearButton.textContent = 'Clear image'
  const preview = document.createElement('img')
  Object.assign(preview.style, {
    display: 'block',
    width: '100%',
    height: '180px',
    marginTop: '8px',
    objectFit: 'contain',
    background: '#151b18',
    borderRadius: '8px',
  })
  const empty = document.createElement('div')
  empty.textContent = 'No image yet. Anim fills this at execution time.'
  Object.assign(empty.style, { marginTop: '8px', fontSize: '12px', opacity: '0.8' })
  const picker = document.createElement('input')
  picker.type = 'file'
  picker.accept = 'image/*'
  picker.style.display = 'none'
  container.append(uploadButton, clearButton, preview, empty, picker)

  const render = () => {
    const fileName = String(imageWidget.value || '').trim()
    preview.style.display = fileName ? 'block' : 'none'
    empty.style.display = fileName ? 'none' : 'block'
    if (fileName) {
      preview.src = inputPreviewUrl(fileName)
      preview.alt = fileName
    } else {
      preview.removeAttribute('src')
    }
  }
  const previousImageCallback = imageWidget.callback
  imageWidget.callback = (...args) => {
    previousImageCallback?.(...args)
    render()
  }
  uploadButton.onclick = () => picker.click()
  clearButton.onclick = () => {
    imageWidget.value = ''
    imageWidget.callback?.('')
    node.setDirtyCanvas?.(true, true)
  }
  picker.onchange = async () => {
    const [file] = Array.from(picker.files || [])
    picker.value = ''
    if (!file) return
    try {
      imageWidget.value = await uploadInputImage(file)
      imageWidget.callback?.(imageWidget.value)
      node.setDirtyCanvas?.(true, true)
    } catch (error) {
      globalThis.alert?.(String(error))
    }
  }
  node.addDOMWidget('anim_image_input_preview', 'div', container, {
    serialize: false,
    hideOnZoom: false,
  })
  node.setSize?.([Math.max(node.size?.[0] || 0, 300), Math.max(node.size?.[1] || 0, 300)])
  render()
}

app.registerExtension({
  name: 'Anim.WorkflowBridge',
  nodeCreated(node) {
    if (node.comfyClass === 'AnimImageReferences') setupImageReferenceBoard(node)
    if (isAnimImageInputClass(node.comfyClass)) setupImageInput(node)
  },
  async setup() { startPushBridge({ app, api, sessionId, tabId }) },
})
