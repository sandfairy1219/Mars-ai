require('dotenv').config();
const { Client, GatewayIntentBits, Events, REST, Routes, SlashCommandBuilder } = require('discord.js');
const { GoogleGenerativeAI } = require('@google/generative-ai');

// ==================== 환경 변수 ====================
const DISCORD_TOKEN = process.env.DISCORD_TOKEN;
const CLIENT_ID = process.env.CLIENT_ID;
const GEMINI_API_KEY = process.env.GEMINI_API_KEY;
const OPENCODEGO_API_KEY = process.env.OPENCODEGO_API_KEY;
const OPENCODEGO_API_URL = process.env.OPENCODEGO_API_URL || 'https://api.opencodego.com/v1/chat/completions';
let defaultProvider = (process.env.DEFAULT_PROVIDER || 'gemini').toLowerCase();

if (!DISCORD_TOKEN) {
  console.error('❌ DISCORD_TOKEN이 .env 파일에 설정되지 않았습니다.');
  process.exit(1);
}
if (!CLIENT_ID) {
  console.error('❌ CLIENT_ID가 .env 파일에 설정되지 않았습니다.');
  process.exit(1);
}

// ==================== 모델 파싱 ====================
// 형식: provider|modelId|displayName
const rawModels = process.env.MODELS || 'gemini|gemini-2.0-flash|Gemini 2.0 Flash';
const availableModels = rawModels.split(',').map(m => m.trim()).filter(Boolean).map(m => {
  const parts = m.split('|');
  return {
    provider: (parts[0] || 'gemini').trim().toLowerCase(),
    modelId: (parts[1] || parts[0]).trim(),
    displayName: (parts[2] || parts[1] || parts[0]).trim()
  };
});

const defaultModelId = process.env.DEFAULT_MODEL || (availableModels[0]?.modelId);

if (availableModels.length === 0) {
  console.error('❌ .env의 MODELS 설정이 잘못되었습니다.');
  process.exit(1);
}

// ==================== Gemini 설정 ====================
let genAI;
const geminiModels = new Map(); // modelId -> GenerativeModel 캐싱

if (GEMINI_API_KEY) {
  genAI = new GoogleGenerativeAI(GEMINI_API_KEY);
  // 기본 모델 미리 초기화
  const geminiDefault = availableModels.find(m => m.provider === 'gemini');
  if (geminiDefault) {
    geminiModels.set(geminiDefault.modelId, genAI.getGenerativeModel({ model: geminiDefault.modelId }));
  }
  console.log(`✅ Gemini API 초기화 완료`);
} else {
  console.warn('⚠️ GEMINI_API_KEY가 설정되지 않았습니다. Gemini 기능은 사용할 수 없습니다.');
}

function getGeminiModel(modelId) {
  if (!genAI) return null;
  if (!geminiModels.has(modelId)) {
    geminiModels.set(modelId, genAI.getGenerativeModel({ model: modelId }));
  }
  return geminiModels.get(modelId);
}

// ==================== 사용자 상태 저장 ====================
const chatHistories = new Map();
const userModels = new Map();      // userId -> 선택한 modelId
const MAX_HISTORY = 10;

function getHistory(userId) {
  if (!chatHistories.has(userId)) chatHistories.set(userId, []);
  return chatHistories.get(userId);
}
function addToHistory(userId, role, content) {
  const history = getHistory(userId);
  history.push({ role, content, timestamp: Date.now() });
  if (history.length > MAX_HISTORY) history.shift();
}
function clearHistory(userId) {
  chatHistories.delete(userId);
}

function getUserModel(userId) {
  if (!userModels.has(userId)) userModels.set(userId, defaultModelId);
  return userModels.get(userId);
}
function setUserModel(userId, modelId) {
  userModels.set(userId, modelId);
}

function findModel(modelId) {
  return availableModels.find(m => m.modelId === modelId);
}

// ==================== AI 호출 ====================
async function askGemini(userId, question, modelId) {
  const model = getGeminiModel(modelId);
  if (!model) return 'Gemini API 키가 설정되지 않았거나 모델을 찾을 수 없습니다.';

  try {
    const history = getHistory(userId);
    const contents = history.map(msg => ({
      role: msg.role === 'user' ? 'user' : 'model',
      parts: [{ text: msg.content }]
    }));

    const chat = model.startChat({
      history: contents
    });

    const result = await chat.sendMessage(question);
    const text = result.response.text();

    addToHistory(userId, 'user', question);
    addToHistory(userId, 'model', text);
    return text;
  } catch (error) {
    console.error('Gemini API 오류:', error);
    return `Gemini API 오류: ${error.message}`;
  }
}

async function askOpenCodeGo(userId, question, modelId) {
  if (!OPENCODEGO_API_KEY) return 'OpenCodeGo API 키가 설정되지 않았습니다.';

  try {
    const history = getHistory(userId);
    const messages = [
      { role: 'system', content: 'You are a helpful assistant.' },
      ...history.map(msg => ({
        role: msg.role === 'user' ? 'user' : 'assistant',
        content: msg.content
      })),
      { role: 'user', content: question }
    ];

    const response = await fetch(OPENCODEGO_API_URL, {
      method: 'POST',
      headers: {
        'Content-Type': 'application/json',
        'Authorization': `Bearer ${OPENCODEGO_API_KEY}`
      },
      body: JSON.stringify({
        model: modelId,
        messages,
        temperature: 0.7,
        max_tokens: 2048
      })
    });

    if (!response.ok) {
      const errorText = await response.text();
      throw new Error(`HTTP ${response.status}: ${errorText}`);
    }

    const data = await response.json();
    const text = data.choices?.[0]?.message?.content || '응답을 받지 못했습니다.';

    addToHistory(userId, 'user', question);
    addToHistory(userId, 'assistant', text);
    return text;
  } catch (error) {
    console.error('OpenCodeGo API 오류:', error);
    return `OpenCodeGo API 오류: ${error.message}`;
  }
}

async function askAI(userId, question, overrideModelId = null) {
  const modelId = overrideModelId || getUserModel(userId);
  const modelInfo = findModel(modelId);

  if (!modelInfo) {
    return `등록되지 않은 모델입니다: ${modelId}\n/models 로 목록을 확인해주세요.`;
  }

  if (modelInfo.provider === 'opencodego') {
    return await askOpenCodeGo(userId, question, modelId);
  }
  return await askGemini(userId, question, modelId);
}

// ==================== 슬래시 커맨드 ====================
// 모델 목록을 Choices로 변환 (최대 25개)
const modelChoices = availableModels.slice(0, 25).map(m => ({
  name: `${m.displayName} (${m.provider})`,
  value: m.modelId
}));

const commands = [
  new SlashCommandBuilder()
    .setName('chat')
    .setDescription('AI에게 질문합니다')
    .addStringOption(option =>
      option.setName('message').setDescription('질문할 내용').setRequired(true)
    )
    .addStringOption(option =>
      option.setName('model')
        .setDescription('사용할 모델 (미입력시 사용자 기본 모델)')
        .setRequired(false)
        .addChoices(...modelChoices)
    ),
  new SlashCommandBuilder()
    .setName('models')
    .setDescription('사용 가능한 모델 목록을 확인합니다'),
  new SlashCommandBuilder()
    .setName('model')
    .setDescription('기본 모델을 변경합니다')
    .addStringOption(option =>
      option.setName('name')
        .setDescription('변경할 모델')
        .setRequired(true)
        .addChoices(...modelChoices)
    ),
  new SlashCommandBuilder()
    .setName('reset')
    .setDescription('대화 기록을 초기화합니다'),
  new SlashCommandBuilder()
    .setName('setprovider')
    .setDescription('기본 AI 제공자를 변경합니다')
    .addStringOption(option =>
      option.setName('provider')
        .setDescription('기본으로 사용할 AI 제공자')
        .setRequired(true)
        .addChoices(
          { name: 'Google Gemini', value: 'gemini' },
          { name: 'OpenCodeGo', value: 'opencodego' }
        )
    )
];

const rest = new REST({ version: '10' }).setToken(DISCORD_TOKEN);

(async () => {
  try {
    console.log('🔄 슬래시 커맨드를 등록하는 중...');
    await rest.put(
      Routes.applicationCommands(CLIENT_ID),
      { body: commands.map(cmd => cmd.toJSON()) }
    );
    console.log('✅ 슬래시 커맨드 등록 완료!');
  } catch (error) {
    console.error('슬래시 커맨드 등록 실패:', error);
  }
})();

// ==================== Discord 클라이언트 ====================
const client = new Client({
  intents: [
    GatewayIntentBits.Guilds,
    GatewayIntentBits.GuildMessages,
    GatewayIntentBits.MessageContent,
    GatewayIntentBits.DirectMessages
  ]
});

client.once(Events.ClientReady, () => {
  console.log(`🤖 봇이 준비되었습니다! ${client.user.tag}`);
  console.log(`🧠 기본 제공자: ${defaultProvider}`);
  console.log(`📝 기본 모델: ${defaultModelId}`);
  console.log(`📋 등록된 모델: ${availableModels.length}개`);
  availableModels.forEach(m => console.log(`   - ${m.displayName} [${m.provider}] (${m.modelId})`));
  console.log('');
  console.log('사용 가능한 명령어:');
  console.log('  /chat [message] [model?] - AI와 대화');
  console.log('  /models - 모델 목록 확인');
  console.log('  /model [name] - 기본 모델 변경');
  console.log('  /reset - 대화 기록 초기화');
  console.log('  /setprovider [gemini|opencodego] - 기본 제공자 변경');
  console.log('  @봇멘션 [message] - 멘션으로 대화');
});

// ==================== 이벤트 핸들러 ====================
client.on(Events.InteractionCreate, async interaction => {
  if (!interaction.isChatInputCommand()) return;
  const { commandName, user } = interaction;

  // /chat
  if (commandName === 'chat') {
    const messageText = interaction.options.getString('message');
    const overrideModel = interaction.options.getString('model');

    try {
      const thinkingMsg = await interaction.reply({
        content: `💭 ${interaction.user.username}님의 질문을 처리하는 중...`,
        fetchReply: true
      });
      await thinkingMsg.react('✅');

      const reply = await askAI(user.id, messageText, overrideModel);

      const isThreadChannel = interaction.channel?.isThread?.() || false;
      if (interaction.channel && !interaction.channel.isDMBased() && !isThreadChannel && thinkingMsg.startThread) {
        const modelInfo = findModel(overrideModel || getUserModel(user.id));
        const threadName = `🤖 ${interaction.user.username} / ${modelInfo?.displayName || 'AI'}`;
        const thread = await thinkingMsg.startThread({
          name: threadName.substring(0, 100),
          autoArchiveDuration: 60
        });

        if (reply.length > 2000) {
          const chunks = reply.match(/.{1,2000}/gs);
          for (const chunk of chunks) await thread.send(chunk);
        } else {
          await thread.send(reply);
        }
        await thinkingMsg.edit(`✅ ${interaction.user.username}님의 질문에 답변했습니다! 👇 쓰레드를 확인해주세요.`);
      } else {
        if (reply.length > 2000) {
          const chunks = reply.match(/.{1,2000}/gs);
          await interaction.editReply(chunks[0]);
          for (let i = 1; i < chunks.length; i++) await interaction.followUp(chunks[i]);
        } else {
          await interaction.editReply(reply);
        }
      }
    } catch (error) {
      console.error('대화 처리 오류:', error);
      await interaction.editReply('오류가 발생했습니다. 다시 시도해주세요.');
    }
  }

  // /models
  if (commandName === 'models') {
    const lines = availableModels.map((m, i) => {
      const isDefault = m.modelId === defaultModelId ? ' ⭐기본' : '';
      const isUserModel = m.modelId === getUserModel(user.id) ? ' 👤선택' : '';
      return `${i + 1}. **${m.displayName}** \`${m.modelId}\` (${m.provider})${isDefault}${isUserModel}`;
    });
    await interaction.reply({
      content: `📋 사용 가능한 모델 목록 (${availableModels.length}개)\n\n${lines.join('\n')}`,
      ephemeral: true
    });
  }

  // /model
  if (commandName === 'model') {
    const selected = interaction.options.getString('name');
    const modelInfo = findModel(selected);
    if (!modelInfo) {
      await interaction.reply({ content: '존재하지 않는 모델입니다.', ephemeral: true });
      return;
    }
    setUserModel(user.id, selected);
    await interaction.reply(`✅ ${interaction.user.username}님의 기본 모델이 **${modelInfo.displayName}** (${modelInfo.provider})로 변경되었습니다.`);
  }

  // /reset
  if (commandName === 'reset') {
    clearHistory(user.id);
    await interaction.reply('🗑️ 대화 기록이 초기화되었습니다.');
  }

  // /setprovider
  if (commandName === 'setprovider') {
    const provider = interaction.options.getString('provider');
    defaultProvider = provider;
    await interaction.reply(`✅ 기본 AI 제공자가 **${provider === 'gemini' ? 'Google Gemini' : 'OpenCodeGo'}**로 변경되었습니다.`);
  }
});

// 멘션/메시지 처리
client.on(Events.MessageCreate, async message => {
  if (message.author.bot) return;

  const isDM = message.channel.isDMBased();
  const isMentioned = message.mentions.has(client.user);
  if (!isDM && !isMentioned) return;

  let content = message.content;
  if (isMentioned && !isDM) {
    content = content.replace(new RegExp(`<@!?${client.user.id}>`, 'g'), '').trim();
  }
  if (!content) return;

  try {
    await message.react('✅');
    const reply = await askAI(message.author.id, content);

    const isThread = message.channel.isThread?.() || false;
    if (!isDM && !isThread && message.startThread) {
      const modelInfo = findModel(getUserModel(message.author.id));
      const threadName = `🤖 ${message.author.username} / ${modelInfo?.displayName || 'AI'}`;
      const thread = await message.startThread({
        name: threadName.substring(0, 100),
        autoArchiveDuration: 60
      });

      if (reply.length > 2000) {
        const chunks = reply.match(/.{1,2000}/gs);
        for (const chunk of chunks) await thread.send(chunk);
      } else {
        await thread.send(reply);
      }
    } else {
      if (reply.length > 2000) {
        const chunks = reply.match(/.{1,2000}/gs);
        await message.reply(chunks[0]);
        for (let i = 1; i < chunks.length; i++) await message.channel.send(chunks[i]);
      } else {
        await message.reply(reply);
      }
    }
  } catch (error) {
    console.error('메시지 처리 오류:', error);
    try { await message.reply('오류가 발생했습니다. 다시 시도해주세요.'); } catch (e) {}
  }
});

// 오류 처리
client.on(Events.Error, error => console.error('Discord 클라이언트 오류:', error));
process.on('unhandledRejection', error => console.error('Unhandled promise rejection:', error));

client.login(DISCORD_TOKEN);
