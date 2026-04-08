class PromptRequest < ApplicationRecord
  enum :status, {
    queued: 0,
    processing: 1,
    completed: 2,
    failed: 3
  }

  validates :prompt_text, presence: true

  LEASE_DEFAULT_SECONDS = 60
  LEASE_MAX_SECONDS = 300
  LONG_POLL_MAX_SECONDS = 30

  scope :claimable, lambda {
    where(
      "status = :queued OR (status = :processing AND (lease_expires_at IS NULL OR lease_expires_at < :now))",
      queued: statuses[:queued],
      processing: statuses[:processing],
      now: Time.current
    )
  }

  def self.next_claim(lease_seconds: LEASE_DEFAULT_SECONDS)
    lease_seconds = [[lease_seconds.to_i, 1].max, LEASE_MAX_SECONDS].min

    transaction do
      request = claimable.order(created_at: :asc).first
      return nil unless request

      request.with_lock do
        now = Time.current
        request.update!(
          status: :processing,
          response_text: nil,
          completed_at: nil,
          failed_at: nil,
          error_message: nil,
          lease_token: SecureRandom.uuid,
          lease_expires_at: now + lease_seconds,
          claimed_at: now,
          attempts: request.attempts + 1
        )
      end

      request
    end
  end

  def self.long_poll_claim(timeout_seconds:, lease_seconds:)
    timeout_seconds = [[timeout_seconds.to_i, 0].max, LONG_POLL_MAX_SECONDS].min
    deadline = Time.current + timeout_seconds

    loop do
      request = next_claim(lease_seconds: lease_seconds)
      return request if request
      return nil if Time.current >= deadline

      sleep(1)
    end
  end

  def valid_lease?(token)
    processing? && lease_token.present? && ActiveSupport::SecurityUtils.secure_compare(lease_token, token.to_s)
  end

  def extend_lease!(lease_seconds: LEASE_DEFAULT_SECONDS)
    lease_seconds = [[lease_seconds.to_i, 1].max, LEASE_MAX_SECONDS].min
    update!(lease_expires_at: Time.current + lease_seconds)
  end

  def append_response_chunk!(chunk)
    return if chunk.blank?

    with_lock do
      update!(response_text: "#{response_text}#{chunk}")
    end
  end

  def finish_success!(response_text:, idempotency_key: nil)
    update!(
      status: :completed,
      response_text: response_text,
      completed_at: Time.current,
      lease_token: nil,
      lease_expires_at: nil,
      error_message: nil,
      result_idempotency_key: idempotency_key
    )
  end

  def finish_failure!(error_message:, idempotency_key: nil)
    update!(
      status: :failed,
      failed_at: Time.current,
      lease_token: nil,
      lease_expires_at: nil,
      error_message: error_message,
      result_idempotency_key: idempotency_key
    )
  end
end