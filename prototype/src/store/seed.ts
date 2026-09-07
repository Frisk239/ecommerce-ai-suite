import type {
  Asset,
  ClipCandidate,
  CoachScenario,
  KnowledgeGap,
  MaterialTask,
  ModelConfig,
  OpsRun,
  Product,
} from './types'

// 种子数据：店铺名是中性演示词，可整包替换（ADR 0008）
export const SHOP_NAME = '演示店铺'

export function makeSeed() {
  const products: Product[] = [
    {
      id: 'P-0001',
      name: '高山天然饮用水 550ml',
      // 食品类目：净含量 + 保质期
      specSchema: ['净含量', '保质期'],
      // 以下两字段由 A-0001 v1 发布时写回（ADR 0010）
      specs: { 净含量: '550ml', 保质期: '12 个月' },
      sellingPoints: ['水源地直灌', '低钠淡矿', '瓶身轻便抗压'],
      stock: 2438,
      stockUnit: '瓶',
    },
    {
      id: 'P-0002',
      name: '钛钢保温杯 500ml',
      // 器皿类目：净含量 + 材质（保温杯不是食品，没有保质期字段）
      specSchema: ['净含量', '材质'],
      // 尚无已发布规格文档写回，保持空，等待 A-0002 发布
      specs: {},
      sellingPoints: ['12 小时保温', '钛钢内胆', '一键开盖'],
      stock: 356,
      stockUnit: '只',
    },
  ]

  const models: ModelConfig[] = [
    {
      id: 'M-01',
      name: 'qwen3.8-flash',
      provider: 'OpenAI 兼容',
      endpoint: 'https://opencode.ai/zen/go/v1',
      params: { temperature: 0.3, maxTokens: 1024 },
      createdAt: '2026-08-01 10:00',
    },
  ]

  const assets: Asset[] = [
    {
      id: 'A-0001',
      kind: '文档',
      title: '高山天然饮用水 · 规格文档',
      state: '已发布',
      productId: 'P-0001',
      sourceKind: '上传',
      sourceNote: '规格-饮用水-v1.docx',
      createdAt: '2026-08-18 10:12',
      machineWash: { status: 'done' },
      publishedV: 1,
      versions: [
        {
          v: 1,
          role: 'published',
          createdAt: '2026-08-18 10:12',
          content:
            '本品取自高山水源地，pH 7.3±0.3，低钠淡矿。净含量 550ml，保质期 12 个月，贮存于阴凉干燥避光处，避免高温暴晒。瓶身为 PET 轻量瓶，抗压设计。',
          fields: [
            { key: '净含量', value: '550ml', confirmed: true, required: true },
            { key: '保质期', value: '12 个月', confirmed: true, required: true },
          ],
        },
      ],
    },
    {
      id: 'A-0002',
      kind: '文档',
      title: '钛钢保温杯 · 规格文档',
      state: '待人洗',
      productId: 'P-0002',
      sourceKind: '上传',
      sourceNote: '供应商提供-保温杯规格.docx',
      createdAt: '2026-09-02 16:40',
      machineWash: { status: 'done' },
      versions: [
        {
          v: 1,
          role: 'review',
          createdAt: '2026-09-02 16:41',
          content:
            '内胆一体成型无焊缝。保温测试：95°C 热水 6 小时后 ≥ 60°C。净含量 500ml。开盖方式为按键弹跳式。原文未标注内胆材质牌号。',
          fields: [
            { key: '净含量', value: '500ml', confirmed: false, required: true },
            // 弃权：原文未找到，禁止编造（ADR 0009）
            { key: '材质', value: null, abstainReason: '原文未找到', confirmed: false, required: true },
          ],
        },
      ],
    },
    {
      id: 'A-0003',
      kind: '文档',
      title: '供应商资质 · 扫描件',
      state: '已接入',
      sourceKind: '上传',
      sourceNote: '供应商群扫描件',
      createdAt: '2026-09-04 09:20',
      machineWash: {
        status: 'failed',
        reason: '无法解析扫描图像：文件为整页图片，未检出文本层。需人工转写后重新机洗。',
      },
      versions: [],
    },
    {
      id: 'A-0004',
      kind: '文档',
      title: '售后与退换货政策',
      state: '已发布',
      sourceKind: '上传',
      sourceNote: '售后政策原文',
      createdAt: '2026-08-10 11:05',
      machineWash: { status: 'done' },
      publishedV: 1,
      versions: [
        {
          v: 1,
          role: 'published',
          createdAt: '2026-08-10 11:05',
          content:
            '自签收之日起 7 天内，商品未使用且包装完整可无理由退货；质量问题 15 天内可退换，运费由店铺承担。食品类拆封后非质量问题不支持退货。退款在收到退货后 3 个工作日内原路退回。',
          fields: [],
        },
      ],
    },
    {
      id: 'A-0005',
      kind: '对话',
      title: '售前咨询 · 保温时长与容量',
      state: '已发布',
      sourceKind: '会话回流',
      sourceNote: 'S-1024',
      createdAt: '2026-08-22 15:02',
      machineWash: { status: 'done' },
      publishedV: 1,
      versions: [
        {
          v: 1,
          role: 'published',
          createdAt: '2026-08-22 15:02',
          content:
            '顾客：这个保温杯早上装的热水，下午还能是热的吗？客服：可以的呢，95 度热水灌进去，6 小时后还能保持在 60 度以上。顾客：那装冰水呢？客服：保冷 12 小时没问题。顾客：杯子多重？客服：净重 210 克，很轻便。',
          fields: [],
        },
      ],
    },
    {
      id: 'A-0006',
      kind: '对话',
      title: '顾客反馈 · 饮用水口感发甜',
      state: '待人洗',
      sourceKind: '会话回流',
      sourceNote: 'S-1077',
      createdAt: '2026-09-03 14:26',
      machineWash: { status: 'done' },
      versions: [
        {
          v: 1,
          role: 'review',
          createdAt: '2026-09-03 14:26',
          content:
            '顾客：这批水喝起来有点发甜，是不是加了东西？客服：亲，我们的水没有添加任何甜味剂，可能是低钠淡矿的口感。顾客：我上次买的就没有这个味道。客服：帮您记录反馈一下批次。',
          fields: [],
        },
      ],
    },
  ]

  const materialTasks: MaterialTask[] = [
    {
      id: 'T-0001',
      productId: 'P-0002',
      brief: '钛钢保温杯 · 种草图文（小红书）',
      status: '待质检',
      output: {
        title: '通勤党的冬天续命杯',
        body:
          '早上灌的咖啡，下班还是温的。钛钢内胆没有金属味，按键一弹就能单手开盖。210 克的重量放包里几乎无感，500ml 刚好一杯的量。',
        refs: [{ assetId: 'A-0005', v: 1 }],
      },
    },
  ]

  const clips: ClipCandidate[] = [
    {
      id: 'C-01',
      timecode: '00:02:14',
      duration: '0:38',
      topic: '保温实测',
      transcript: '现场实测：早上九点灌的 95 度热水，现在下午三点，温度计显示还有 63 度。',
      productId: 'P-0002',
    },
    {
      id: 'C-02',
      timecode: '00:05:40',
      duration: '0:52',
      topic: '开盖演示',
      transcript: '单手按键弹跳开盖，杯盖可以当小杯子用，办公室场景特别方便。',
      productId: 'P-0002',
    },
    {
      id: 'C-03',
      timecode: '00:08:02',
      duration: '0:29',
      topic: '重量对比',
      transcript: '和手机放一起对比，净重 210 克，比同样容量的保温杯轻了三分之一。',
      productId: 'P-0002',
    },
    {
      id: 'C-04',
      timecode: '00:11:26',
      duration: '0:44',
      topic: '内胆材质',
      transcript: '钛钢内胆一体成型，没有焊缝，泡柠檬水也不怕腐蚀。',
      productId: 'P-0002',
    },
    {
      id: 'C-05',
      timecode: '00:14:05',
      duration: '0:33',
      topic: '饮用水水源',
      transcript: '镜头带到水源地：海拔 3800 米，低钠淡矿，直接灌装不打添加剂。',
      productId: 'P-0001',
    },
    {
      id: 'C-06',
      timecode: '00:16:48',
      duration: '0:47',
      topic: '瓶身抗压',
      transcript: '现场踩给各位看，瓶身轻但抗压，整箱堆放不变形。',
      productId: 'P-0001',
    },
    {
      id: 'C-07',
      timecode: '00:19:30',
      duration: '0:36',
      topic: '价格机制',
      transcript: '今天直播间下单两箱送一箱，拍的时候注意选规格。',
      productId: 'P-0001',
    },
    {
      id: 'C-08',
      timecode: '00:22:11',
      duration: '0:41',
      topic: '保冷测试',
      transcript: '装冰水放车里晒了一下午，打开还是冰的，保冷 12 小时靠谱。',
      productId: 'P-0002',
    },
  ]

  const opsRun: OpsRun = {
    productId: 'P-0002',
    steps: [
      {
        key: 'read-product',
        name: '读取商品卖点',
        via: '中台接口 · 商品读取',
        detail: 'P-0002 钛钢保温杯：12 小时保温 / 钛钢内胆 / 一键开盖',
        status: 'pending',
      },
      {
        key: 'gen-material',
        name: '调用素材中心生成文案',
        via: '中台接口 · 素材任务',
        detail: '',
        status: 'pending',
      },
      {
        key: 'compose',
        name: '组装投放文案',
        via: '中台接口 · 已发布素材引用',
        detail: '',
        status: 'pending',
      },
    ],
    delivery: null,
  }

  const coachScenarios: CoachScenario[] = [
    {
      id: 'SC-01',
      source: { assetId: 'A-0005', v: 1 },
      title: '保温时长追问',
      opening: '你好，我早上七点装的热水，到中午十二点还能喝到热的吗？',
      rubric: [
        { dim: '口径准确', max: 40 },
        { dim: '证据引用', max: 30 },
        { dim: '服务语气', max: 30 },
      ],
    },
    {
      id: 'SC-02',
      source: { assetId: 'A-0005', v: 1 },
      title: '容量与重量连问',
      opening: '杯子多重？装冰水能凉多久？',
      rubric: [
        { dim: '口径准确', max: 40 },
        { dim: '证据引用', max: 30 },
        { dim: '服务语气', max: 30 },
      ],
    },
  ]

  const knowledgeGaps: KnowledgeGap[] = [
    {
      id: 'G-0001',
      question: '这杯能装开水吗？',
      productId: 'P-0002',
      createdAt: '2026-09-05 11:20',
      status: 'open',
    },
  ]

  return { products, assets, models, materialTasks, clips, opsRun, coachScenarios, knowledgeGaps }
}
