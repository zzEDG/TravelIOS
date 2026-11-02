import { Modal } from 'ant-design-vue'

let open = false
export function showError(message: string) {
  if (!message || open) return
  open = true
  Modal.error({
    title: '操作未完成',
    content: message,
    okText: '知道了',
    onOk: () => { open = false },
    afterClose: () => { open = false },
  })
}
