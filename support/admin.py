from django.contrib import admin

from .models import Conversation, Message


class MessageInline(admin.TabularInline):
    model = Message
    extra = 0
    fields = ("created", "from_staff", "author", "text", "attachment", "read_at")
    readonly_fields = fields


@admin.register(Conversation)
class ConversationAdmin(admin.ModelAdmin):
    list_display = ("user", "status", "updated")
    list_filter = ("status",)
    search_fields = ("user__email",)
    inlines = [MessageInline]
